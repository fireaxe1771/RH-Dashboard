from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime

# --- SUB-MODELS ---

class WidgetLayout(BaseModel):
    """Specifies the visual positioning parameters in the dashboard CSS grid."""
    x: int = Field(..., description="X coordinate of widget (0-11)")
    y: int = Field(..., description="Y coordinate of widget")
    w: int = Field(..., description="Width columns span (1-12)")
    h: int = Field(..., description="Height units span")

class WidgetConfig(BaseModel):
    """Visual properties of a dashboard chart or iframe embed."""
    xAxisKey: Optional[str] = Field(None, description="Column mapping for chart X labels")
    yAxisKeys: Optional[List[str]] = Field(None, description="Column mappings for chart Y numeric values")
    colors: Optional[List[str]] = Field(None, description="Hex color strings representing paths/bars")
    embedUrl: Optional[str] = Field(None, description="Google Looker Studio sandbox iframe URL")
    format: Optional[str] = Field(None, description="Formatting category: 'currency', 'percentage', or null")

class Widget(BaseModel):
    """Defines a metric statistic card, chart query, or iframe report."""
    id: str = Field(..., description="Unique client-side identifier")
    title: str = Field(..., description="Header text display of card")
    type: str = Field(..., description="stat, line, bar, pie, table, or looker")
    sql_query: Optional[str] = Field(None, description="Read-only T-SQL target query")
    layout: WidgetLayout
    config: WidgetConfig

# --- DATABASE MODELS ---

class DashboardBase(BaseModel):
    """Shared dashboard definition properties."""
    name: str = Field(..., min_length=1, max_length=100, description="Title of the dashboard page")
    description: Optional[str] = Field(None, max_length=500, description="Helpful summary statement")
    widgets: List[Widget] = Field(default_factory=list, description="Array of widget specifications")

class DashboardCreate(DashboardBase):
    """Payload to insert a new dashboard page."""
    pass

class DashboardResponse(DashboardBase):
    """API return response structure for a dashboard."""
    id: str = Field(..., alias="_id", description="MongoDB ObjectId hex string")
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda dt: dt.isoformat()
        }

# --- QUERY & FILTER MODELS ---

class DashboardFilters(BaseModel):
    """Global query parameters that dynamically filter visual queries."""
    department_id: Optional[str] = None
    processor_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class SQLQueryRequest(BaseModel):
    """Payload to execute visual queries against target database."""
    sql_query: str = Field(..., description="Select query statement to execute")
    filters: Optional[DashboardFilters] = None

class DrillDownRequest(BaseModel):
    """Payload to query operational records matching a clicked visualization coordinate."""
    field_name: str = Field(..., description="Column key that was clicked (e.g. Status, Department)")
    field_value: Any = Field(..., description="Coordinate value that was clicked (e.g. Draft, Metro Fire)")
    filters: Optional[DashboardFilters] = None

# --- USER MANAGEMENT MODELS ---

class UserBase(BaseModel):
    email: EmailStr
    name: str

class UserCreate(UserBase):
    pass

class UserResponse(UserBase):
    id: str = Field(..., alias="_id")
    role: str = "viewer"
    created_at: datetime

    class Config:
        populate_by_name = True
