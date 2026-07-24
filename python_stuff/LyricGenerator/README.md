# Python 3.13 Audioop Compatibility Project

This project includes a compatibility layout/patch to handle the removal of the standard `audioop` module in Python 3.13+. It forces transitively dependent libraries like `pydub` and `shazamio` to use the `audioop-lts` package instead.

## Installation

To install the required dependencies for this project, run the following command in your terminal:

```bash
pip install -r requirements.txt
```

## Recommended Setup (Virtual Environment)

It is highly recommended to install these dependencies inside a isolated virtual environment to prevent conflicts with other Python packages on your system.

### 1. Create a Virtual Environment
```bash
# On Windows
python -m venv venv

# On macOS/Linux
python3 -m venv venv
```

### 2. Activate the Virtual Environment
```bash
# On Windows (Command Prompt)
venv\Scripts\activate.bat

# On Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# On macOS/Linux
source venv/bin/activate
```

### 3. Install Requirements
Once the environment is activated, run the installation command:
```bash
pip install -r requirements.txt
```
