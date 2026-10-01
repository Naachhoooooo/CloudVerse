# CloudVerse ☁️

CloudVerse is an advanced, production-ready Multi-Process Telegram Upload Bot ecosystem. It provides robust, resilient, and high-performance file transferring utilities between Telegram environments and popular cloud services like Google Drive, with scaffolded support for Mega and Rclone.

## 🏗️ Architecture Design

CloudVerse employs a **Monorepo with Multi-Process Runners** architecture approach. The design establishes a strict boundary between the global `shared/` kernel and independent operational domains inside `bots/` and `support/`.

*   **`shared/`**: Contains provider-agnostic core libraries, handlers, and centralized manager systems (Cryptography, Memory, and Global Database Pool via `DatabaseConnectionManager`).
*   **`bots/drive/`**: The primary production-grade Google Drive uploading service.
*   **`bots/mega/`**: Boilerplate staging layout for Mega uploads.
*   **`bots/rclone/`**: Boilerplate staging layout for diverse cloud integrations using Rclone.
*   **`support/`**: Custom user-admin reporting ecosystem utilizing a ticketing queue.

## ✨ Features

*   **Dynamic Lane Capacity**: Intelligently switches resources based on real-time server state evaluation (CPU, Memory, Disk I/O).
*   **Multi-Queue Tracking**: Separation for administrators and regular users to ensure maximum stability and reliability.
*   **Resilient Database Pooling**: Asynchronous SQLite pooling to prevent write locks during bursts of upload metadata handling. 
*   **Encrypted Sessions**: Critical session tokens and Google Drive authentication configurations are secured via `cryptography.fernet` using `EncryptionManager`.
*   **Detailed Analytics & Telemetry**: Powered by `MemoryManager` and rolling logs with 30-day retention policies.

## ⚙️ Configuration & Environment

The project relies rigidly on `.env` variable configuration rather than code-based values for sensitive constraints.

1.  Copy `.env.example` to `.env` in the root logic directory.
2.  Follow the comprehensive `generation tips` inside the `.env.example` setup to obtain correct formats for variables like:
    *   `MASTER_PASSWORD`
    *   `SALT`
    *   `TELEGRAM_BOT_TOKEN_DRIVE`

## 🚀 Setup & Execution

### Prerequisites
- Python 3.9+
- SQLite3 (Pre-installed dynamically through Python's `sqlite3` built-in package)

### Installation
```bash
# Clone repository
git clone https://github.com/naachhoooooo/cloudverse.git
cd cloudverse

# Setting up Virtual Environment
python -m venv .venv

# On Linux/macOS
source .venv/bin/activate
# On Windows
.venv\Scripts\activate

# Install Dependencies
pip install -r requirements.txt
```

### Starting the Watchdog Orchestrator

CloudVerse handles parallel multi-bot execution via the `run_all.py` orchestrator script. Never execute individual bots alongside each other manually utilizing `asyncio.gather`. 

```bash
python run_all.py
```

## 🧪 Testing

The codebase heavily enforces Unit Testing patterns. You can expand test capabilities under the `/tests` boundary using standard `pytest`.

```bash
# Run all tests natively
pytest tests/unit/ -v
```

## 📜 Development Conventions

Please examine the `.agent/rules/` directives before pushing changes:
*   `architecture.md`: Maintain domain logic boundaries securely.
*   `code-review-and-refactor.md`: Readability and standard complexity thresholding.
*   `testing.md`: Validates CI/CD thresholds.
*   `logs-and-logging.md`: Ensures JSON structured telemetry.

## 📄 License
*Closed Source* / All Rights Reserved.
