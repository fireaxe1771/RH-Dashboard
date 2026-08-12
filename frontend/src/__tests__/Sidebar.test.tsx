import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { Sidebar } from '../components/Sidebar';
import { Dashboard } from '../services/api';

// Mock the AuthContext hook
vi.mock('../components/AuthContext', () => {
  return {
    useAuth: () => ({
      user: { name: 'John Doe', email: 'john.doe@streamlineas.com' },
      logout: vi.fn(),
    })
  };
});

describe('Sidebar Component', () => {
  const mockDashboards: Dashboard[] = [
    { id: 'dash-1', name: 'Claims Flow Monitor', widgets: [] },
    { id: 'dash-2', name: 'Billing Analytics', widgets: [] }
  ];

  test('renders dashboards list correctly', () => {
    render(
      <Sidebar 
        dashboards={mockDashboards} 
        selectedId="dash-1" 
        onSelect={vi.fn()} 
        onNew={vi.fn()} 
        isDesignerOpen={false} 
      />
    );
    
    expect(screen.getByText('Claims Flow Monitor')).toBeInTheDocument();
    expect(screen.getByText('Billing Analytics')).toBeInTheDocument();
    expect(screen.getByText('John Doe')).toBeInTheDocument();
    expect(screen.getByText('john.doe@streamlineas.com')).toBeInTheDocument();
  });

  test('triggers onSelect when clicking a dashboard', () => {
    const handleSelect = vi.fn();
    render(
      <Sidebar 
        dashboards={mockDashboards} 
        selectedId="dash-1" 
        onSelect={handleSelect} 
        onNew={vi.fn()} 
        isDesignerOpen={false} 
      />
    );

    const secondDashBtn = screen.getByText('Billing Analytics');
    fireEvent.click(secondDashBtn);
    expect(handleSelect).toHaveBeenCalledWith('dash-2');
  });

  test('places static dashboards above saved dashboards and removes duplicate system entries', () => {
    render(
      <Sidebar
        dashboards={[
          { id: 'system-1', name: 'Claims Breakdown', created_by: 'system', widgets: [] },
          { id: 'system-2', name: 'Claims Breakdown', created_by: 'system', widgets: [] },
          { id: 'saved-1', name: 'My Dashboard', widgets: [] },
        ]}
        selectedId="system-1"
        onSelect={vi.fn()}
        onNew={vi.fn()}
        isDesignerOpen={false}
        onSelectAiAdoption={vi.fn()}
      />
    );

    expect(screen.getAllByText('Claims Breakdown')).toHaveLength(1);
    // Folder headers are always visible
    expect(screen.getByText('AI Analytics')).toBeInTheDocument();
    expect(screen.getByText('Azure Billing')).toBeInTheDocument();
    expect(screen.getByText('Saved Dashboards')).toBeInTheDocument();
    expect(screen.getByText('My Dashboard')).toBeInTheDocument();

    // Folders are collapsed by default — children must NOT be rendered
    expect(screen.queryByText('AI Adoption')).not.toBeInTheDocument();
    expect(screen.queryByText('Cost Overview')).not.toBeInTheDocument();

    const sidebar = screen.getByTestId('sidebar');
    const sidebarText = sidebar.textContent ?? '';
    expect(sidebarText.indexOf('AI Analytics')).toBeLessThan(sidebarText.indexOf('Saved Dashboards'));
    expect(sidebarText.indexOf('Azure Billing')).toBeLessThan(sidebarText.indexOf('Saved Dashboards'));
    expect(sidebarText.indexOf('Claims Breakdown')).toBeLessThan(sidebarText.indexOf('Saved Dashboards'));
  });

  test('expanding the AI Analytics folder reveals the AI Adoption child item', () => {
    render(
      <Sidebar
        dashboards={[]}
        selectedId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        isDesignerOpen={false}
        onSelectAiAdoption={vi.fn()}
      />
    );

    // Collapsed by default (aiAdoptionOpen defaults to false)
    expect(screen.queryByText('AI Adoption')).not.toBeInTheDocument();

    // Click the folder header to expand
    fireEvent.click(screen.getByText('AI Analytics'));
    expect(screen.getByText('AI Adoption')).toBeInTheDocument();
  });

  test('AI Analytics folder auto-expands when aiAdoptionOpen is true', () => {
    render(
      <Sidebar
        dashboards={[]}
        selectedId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        isDesignerOpen={false}
        aiAdoptionOpen={true}
        onSelectAiAdoption={vi.fn()}
      />
    );

    // Folder auto-expands so the active AI Adoption item is visible
    expect(screen.getByText('AI Adoption')).toBeInTheDocument();
  });

  test('triggers onNew when clicking New Dashboard button', () => {
    const handleNew = vi.fn();
    render(
      <Sidebar 
        dashboards={mockDashboards} 
        selectedId={null} 
        onSelect={vi.fn()} 
        onNew={handleNew} 
        isDesignerOpen={false} 
      />
    );

    const newDashBtn = screen.getByText('New Dashboard');
    fireEvent.click(newDashBtn);
    expect(handleNew).toHaveBeenCalledTimes(1);
  });
});
