# RecoveryHub Dashboard System

A modern web-based dashboard system for visualizing emergency fire runs, claims, and invoices data. Built with FastAPI backend, React frontend, and MongoDB metadata store with Azure SQL as the target data source.

## Architecture

- **Backend**: Python FastAPI with MongoDB for metadata storage
- **Frontend**: React + TypeScript with Vite, featuring Azure AD authentication
- **Database**: MongoDB (metadata) + Azure SQL (target data)
- **Infrastructure**: Docker Compose for local development, Terraform for Azure deployment
- **Authentication**: Microsoft Entra ID (Azure AD) integration

## Prerequisites

- Docker and Docker Compose
- Azure SQL Database with connection details
- Azure AD Application registration for authentication
- (Optional) MongoDB Atlas account for production deployment

## Quick Start (Local Development)

### 1. Environment Configuration

Copy the example environment file and update with your credentials:

```bash
cp .env.example .env
```

Update `.env` with your actual values:
- Azure SQL connection details (host, database, user, password)
- Azure AD Client ID and Tenant ID
- MongoDB URI (if using MongoDB Atlas instead of local Docker)

### 2. Start the Application

Use Docker Compose to start all services:

```bash
docker-compose up --build
```

This will start:
- MongoDB on port 27017 (local container)
- Backend API on port 8000
- Frontend web app on port 3000

### 3. Access the Application

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Documentation: http://localhost:8000/docs

## Development Setup

### Backend Development

The backend can be run locally without Docker for development:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend Development

The frontend can be run locally for development:

```bash
cd frontend
npm install
npm run dev
```

The development server will start on http://localhost:5173

## Testing

### Backend Tests

```bash
cd backend
pytest
```

### Frontend Tests

```bash
cd frontend
npm test
```

## Project Structure

```
.
├── backend/                 # FastAPI backend application
│   ├── main.py             # API endpoints and application setup
│   ├── models.py           # Pydantic models
│   ├── database.py         # MongoDB connection
│   ├── target_db.py        # Azure SQL integration
│   ├── auth.py             # Azure AD authentication
│   ├── config.py           # Configuration management
│   ├── tests/              # Backend unit tests
│   └── requirements.txt    # Python dependencies
├── frontend/               # React frontend application
│   ├── src/                # React components and source code
│   ├── public/             # Static assets
│   ├── package.json        # Node.js dependencies
│   └── vite.config.ts      # Vite configuration
├── terraform/              # Infrastructure as Code for Azure
├── .env.example            # Environment configuration template
├── .env                    # Your environment configuration (not in git)
├── docker-compose.yml      # Docker services configuration
└── README.md              # This file
```

## API Endpoints

The backend provides the following main endpoints:

- `POST /api/dashboards` - Create a new dashboard
- `GET /api/dashboards` - List all dashboards
- `GET /api/dashboards/{id}` - Get a specific dashboard
- `PUT /api/dashboards/{id}` - Update a dashboard
- `DELETE /api/dashboards/{id}` - Delete a dashboard
- `POST /api/query/execute` - Execute SQL queries
- `GET /api/query/schema` - Get database schema
- `GET /api/query/filters` - Get filter options
- `POST /api/query/drilldown` - Execute drilldown queries

## Configuration

### Environment Variables

See `.env.example` for all available configuration options:

- `PORT` - Backend server port (default: 8000)
- `MONGODB_URI` - MongoDB connection string
- `MONGODB_DB_NAME` - MongoDB database name
- `AZURE_SQL_HOST` - Azure SQL server hostname
- `AZURE_SQL_PORT` - Azure SQL port (default: 1433)
- `AZURE_SQL_DB` - Azure SQL database name
- `AZURE_SQL_USER` - Azure SQL username
- `AZURE_SQL_PASSWORD` - Azure SQL password
- `AZURE_CLIENT_ID` - Azure AD application client ID
- `AZURE_TENANT_ID` - Azure AD tenant ID
- `VITE_AZURE_CLIENT_ID` - Azure AD client ID (frontend)
- `VITE_AZURE_TENANT_ID` - Azure AD tenant ID (frontend)

## Azure Deployment

The project includes Terraform configurations for deploying to Azure:

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

## Troubleshooting

### MongoDB Connection Issues

If using local MongoDB via Docker Compose, ensure the mongo container is running:
```bash
docker-compose ps mongo
```

### Azure SQL Connection Issues

Verify your Azure SQL firewall allows access from your IP address and that the credentials in `.env` are correct.

### Authentication Issues

Ensure your Azure AD application is properly configured with the correct redirect URLs:
- Local development: `http://localhost:3000`
- Production: Your production frontend URL

## License

[Your License Here]