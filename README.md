# Elite Breakout System

An enterprise-grade, fully governed quantitative trading platform and momentum scanner for the National Stock Exchange (NSE).

## Quick Start

### Installation
```bash
# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration
Ensure your environment variables are configured in `.env`:
```env
FYERS_APP_ID="your_app_id"
FYERS_SECRET_KEY="your_secret"
SCRAPERAPI_KEY="your_scraperapi_key"
DATABASE_URL="postgres://..."
```

### Running the System
```bash
python3 app/main.py
```

## Documentation

The architecture and engineering specifications for this project are formally maintained in two canonical documents. **No architectural descriptions belong in this README.**

1. **[System Guide](docs/SYSTEM_GUIDE.md)**: The operational and architectural guide. Read this to understand how the system executes, how it manages state, and how to debug it.
2. **[Engineering Specification](docs/ENGINEERING_SPECIFICATION.md)**: The rebuild guide. Read this to understand the data contracts, threading models, architectural invariants, and exact specifications necessary to reconstruct the platform.

*For versioned architectural changes, see the [Changelog](docs/CHANGELOG.md).*