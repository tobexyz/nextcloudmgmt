#!/bin/bash

# Configuration
VENV_DIR="venv"
PYTHON_BIN="python3"
REQUIREMENTS="requirements.txt"
SCRIPT_NAME="setup_working_group.py"

echo "------------------------------------------------"
echo "🌐 NGO Nextcloud Admin Toolkit - Preparation"
echo "------------------------------------------------"

# 1. Check if Python is installed
if ! command -v $PYTHON_BIN &> /dev/null; then
    echo "❌ Error: $PYTHON_BIN is not installed. Please install Python first."
    exit 1
fi

# 2. Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment in /$VENV_DIR..."
    $PYTHON_BIN -m venv $VENV_DIR
else
    echo "✅ Virtual environment already exists."
fi

# 3. Activate environment
source $VENV_DIR/bin/activate

# 4. Install/Update requirements
if [ -f "$REQUIREMENTS" ]; then
    echo "📥 Installing dependencies from $REQUIREMENTS..."
    pip install --upgrade pip
    pip install -r $REQUIREMENTS
else
    echo "⚠️ Warning: $REQUIREMENTS not found. Attempting to install core packages directly..."
fi

echo "------------------------------------------------"
echo "🚀 Environment ready. Launching $SCRIPT_NAME..."
echo "------------------------------------------------"

# 5. Run the actual Python script
python $SCRIPT_NAME

# Deactivate after script finishes
deactivate
