import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { Navbar } from '../components/Navbar';

describe('Navbar Component', () => {
  test('renders title and description correctly', () => {
    render(
      <Navbar 
        title="Emergency Runs Overview" 
        description="Monitoring claims from metro fire department" 
      />
    );
    
    expect(screen.getByText('Emergency Runs Overview')).toBeInTheDocument();
    expect(screen.getByText('Monitoring claims from metro fire department')).toBeInTheDocument();
  });

  test('calls onRefresh when sync button is clicked', () => {
    const handleRefresh = vi.fn();
    render(<Navbar title="Test Dash" onRefresh={handleRefresh} />);
    
    const syncButton = screen.getByRole('button', { name: /sync/i });
    expect(syncButton).toBeInTheDocument();
    
    fireEvent.click(syncButton);
    expect(handleRefresh).toHaveBeenCalledTimes(1);
  });

  test('displays correct database status messages', () => {
    const { rerender } = render(<Navbar title="Test" dbStatus="connected" />);
    expect(screen.getByText('Live Online')).toBeInTheDocument();

    rerender(<Navbar title="Test" dbStatus="testing" />);
    expect(screen.getByText('Connecting...')).toBeInTheDocument();

    rerender(<Navbar title="Test" dbStatus="error" />);
    expect(screen.getByText('Disconnected')).toBeInTheDocument();
  });
});
