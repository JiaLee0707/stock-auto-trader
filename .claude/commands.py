{
  "commands": [
    {
      "name": "setup",
      "description": "Initialize project structure and install dependencies",
      "command": "python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    },
    {
      "name": "run",
      "description": "Start the trading bot",
      "command": "python main.py"
    },
    {
      "name": "test",
      "description": "Test Python syntax and basic functionality",
      "command": "python3 -m py_compile *.py && echo 'Syntax check passed'"
    },
    {
      "name": "logs",
      "description": "Show recent bot logs",
      "command": "tail -20 logs/bot.log 2>/dev/null || echo 'No logs found'
    }
  ]
}