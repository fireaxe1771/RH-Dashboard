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
