import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SyncHealthIndicator } from '../components/ai/SyncHealthIndicator';
import { aiAnalyticsApi } from '../services/aiAnalyticsApi';
import type { AiSyncHealth, AiDeadLetter } from '../services/aiAnalyticsApi';

// Mock the API
vi.mock('../services/aiAnalyticsApi', () => ({
  aiAnalyticsApi: {
    getSyncHealth: vi.fn(),
    getDeadLetters: vi.fn(),
    resolveDeadLetter: vi.fn(),
  },
}));

const mockGetSyncHealth = aiAnalyticsApi.getSyncHealth as ReturnType<typeof vi.fn>;
const mockGetDeadLetters = aiAnalyticsApi.getDeadLetters as ReturnType<typeof vi.fn>;
const mockResolveDeadLetter = aiAnalyticsApi.resolveDeadLetter as ReturnType<typeof vi.fn>;

const SYNCED_HEALTH: AiSyncHealth = {
  status: 'synced',
  worker_enabled: true,
  worker_status: 'running',
  last_started_at: '2026-08-13T12:00:00Z',
  last_successful_event_at: '2026-08-13T12:05:00Z',
  last_checkpoint_at: '2026-08-13T12:04:00Z',
  consecutive_error_count: 0,
  sync_integrity: {
    last_check_at: '2026-08-13T12:04:30Z',
    check_in_progress: false,
    source_count: 18005,
    projection_count: 18005,
    count_mismatch: false,
    divergent_count: 0,
    missing_count: 0,
    last_error: null,
  },
  metrics: {
    events_received: 1000,
    claims_refreshed: 950,
    projections_created: 100,
    projections_updated: 850,
    dead_letters_created: 0,
    sync_integrity_checks: 12,
    sync_integrity_divergent_found: 0,
  },
  last_error: null,
};

const ERROR_HEALTH: AiSyncHealth = {
  ...SYNCED_HEALTH,
  status: 'error',
  worker_status: 'error',
  consecutive_error_count: 3,
  last_error: 'MongoDB connection lost',
  sync_integrity: {
    ...SYNCED_HEALTH.sync_integrity,
    last_error: 'integrity check failed',
  },
};

const CATCHING_UP_HEALTH: AiSyncHealth = {
  ...SYNCED_HEALTH,
  status: 'catching-up',
  sync_integrity: {
    ...SYNCED_HEALTH.sync_integrity,
    divergent_count: 3,
    missing_count: 1,
    count_mismatch: true,
    source_count: 18008,
    projection_count: 18005,
  },
};

const DEAD_LETTER: AiDeadLetter = {
  _id: '507f1f77bcf86cd799439011',
  claim_id: 12345,
  source_event_type: 'update',
  error_type: 'ValueError',
  error_message: 'Cannot upsert projection with _id=None',
  first_failed_at: '2026-08-13T10:00:00Z',
  last_failed_at: '2026-08-13T11:00:00Z',
  attempt_count: 3,
  worker_version: '0.1.0',
  resolved: false,
};

beforeEach(() => {
  mockGetSyncHealth.mockResolvedValue(SYNCED_HEALTH);
  mockGetDeadLetters.mockResolvedValue([]);
  mockResolveDeadLetter.mockResolvedValue({ resolved: true, claim_id: 12345, updated: 1 });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('SyncHealthIndicator', () => {
  it('should render loading state initially', () => {
    mockGetSyncHealth.mockReturnValue(new Promise(() => {}));
    render(<SyncHealthIndicator />);
    expect(screen.getByText('Checking sync status…')).toBeInTheDocument();
  });

  it('should render synced status badge', async () => {
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('In Sync')).toBeInTheDocument();
    });
  });

  it('should render error status badge', async () => {
    mockGetSyncHealth.mockResolvedValue(ERROR_HEALTH);
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('Sync Error')).toBeInTheDocument();
    });
  });

  it('should render catching-up status badge', async () => {
    mockGetSyncHealth.mockResolvedValue(CATCHING_UP_HEALTH);
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('Catching Up')).toBeInTheDocument();
    });
  });

  it('should render stopped status badge when worker disabled', async () => {
    mockGetSyncHealth.mockResolvedValue({
      ...SYNCED_HEALTH,
      status: 'stopped',
      worker_enabled: false,
    });
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('Sync Stopped')).toBeInTheDocument();
    });
  });

  it('should show dead-letter count badge when dead letters exist', async () => {
    mockGetDeadLetters.mockResolvedValue([DEAD_LETTER]);
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText(/1 dead-letter/)).toBeInTheDocument();
    });
  });

  it('should expand detail panel on click', async () => {
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('In Sync')).toBeInTheDocument();
    });

    expect(screen.queryByText('Source:')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('In Sync'));

    await waitFor(() => {
      expect(screen.getByText('Source:')).toBeInTheDocument();
    });
  });

  it('should show error message in expanded panel', async () => {
    mockGetSyncHealth.mockResolvedValue(ERROR_HEALTH);
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('Sync Error')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Sync Error'));

    await waitFor(() => {
      expect(screen.getByText(/MongoDB connection lost/)).toBeInTheDocument();
    });
  });

  it('should show dead-letter list with resolve button in expanded panel', async () => {
    mockGetDeadLetters.mockResolvedValue([DEAD_LETTER]);
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText(/1 dead-letter/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/1 dead-letter/));

    await waitFor(() => {
      expect(screen.getByText('#12345')).toBeInTheDocument();
      expect(screen.getByText('Resolve')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Resolve'));
    expect(mockResolveDeadLetter).toHaveBeenCalledWith(12345);
  });

  it('should show divergence stats when catching up', async () => {
    mockGetSyncHealth.mockResolvedValue(CATCHING_UP_HEALTH);
    render(<SyncHealthIndicator />);
    await waitFor(() => {
      expect(screen.getByText('Catching Up')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Catching Up'));

    await waitFor(() => {
      expect(screen.getByText('Divergent:')).toBeInTheDocument();
      expect(screen.getByText('Missing:')).toBeInTheDocument();
    });
  });
});
