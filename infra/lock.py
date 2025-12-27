import socket
import logging
import sys
import os

# Unique port for your bot to claim. 
# Changing this allows running a 2nd separate instance of the bot if needed.
LOCK_PORT = 20001 

logger = logging.getLogger("Lock")
_lock_socket = None

def acquire_lock():
    """
    Binds to a local UDP port to ensure only ONE instance of the bot runs.
    Returns: True if successful.
    Raises: RuntimeError if another instance is detected.
    """
    global _lock_socket
    
    # 1. Idempotency Check (If we already have it, don't fail)
    if _lock_socket:
        return True

    try:
        # 2. Create UDP Socket (Lightweight lock)
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Allow immediate reuse of the port after a clean exit
        try:
            _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except (AttributeError, socket.error): 
            pass

        # 3. Attempt Bind (The actual locking mechanism)
        # We bind to 127.0.0.1 to prevent triggering Windows Firewall popups for public networks
        _lock_socket.bind(('127.0.0.1', LOCK_PORT))
        
        logger.info(f"🔒 Single-Instance Lock acquired on Port {LOCK_PORT} (PID: {os.getpid()}).")
        return True

    except socket.error:
        # 4. Intelligent Error Reporting
        logger.critical(f"⛔ FATAL: Port {LOCK_PORT} is busy. The bot is ALREADY running.")
        
        # OS-Specific Hints for the User
        if sys.platform == 'win32':
            logger.critical(f"💡 Windows Tip: Run 'netstat -ano | findstr {LOCK_PORT}' in cmd to find the PID, then kill it in Task Manager.")
        else:
            logger.critical(f"💡 Linux Tip: Run 'lsof -i :{LOCK_PORT}' or 'pkill -f main.py' to clean up.")
            
        raise RuntimeError("Another instance is already running.")
    
    except Exception as e:
        logger.critical(f"❌ Unexpected Lock Error: {e}")
        raise e

def release_lock():
    """
    Cleanly releases the port so the bot can be restarted immediately.
    """
    global _lock_socket
    if _lock_socket:
        try:
            _lock_socket.close()
            _lock_socket = None
            logger.info("🔓 Lock released.")
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")