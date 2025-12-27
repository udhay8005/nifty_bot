
### 1. Short Tagline (For the "About" section on the right sidebar)

> **Intelligent Nifty Option Buying Bot (Upstox + Telegram)**
> A robust Python algorithmic trading bot for NSE Nifty 50 Options. Features automated 9:30 AM breakout strategy, trailing stop-loss, Telegram remote control, and a resilient crash-recovery system.

---

### 2. Standard Description (For the `README.md` header)

# 🚀 Intelligent Nifty Option Bot

A fully automated, high-frequency algorithmic trading bot designed for **Nifty 50 Options Buying** on the NSE. Built with **Python** and the **Upstox V2 API**, this system prioritizes discipline, safety, and risk management over raw speed.

It implements a strict **9:30 AM Breakout Strategy** with "Set & Forget" logic that manages the trade lifecycle—from entry to trailing stop-loss to exit—completely autonomously.

**Key Capabilities:**

* 🧠 **Intelligent Execution:** Auto-calculates strikes based on premium targets (e.g., ₹180) and manages 9:45 AM "SL-to-Cost" rules.
* 📱 **Telegram Command Center:** Monitor status, tune strategy parameters (Target/SL), and trigger the Emergency Kill Switch directly from your phone.
* 🛡️ **Safety Watchdog:** A dedicated failsafe system that monitors `LTP` vs `SL` every second and force-exits positions if broker orders fail.
* 🧪 **Hybrid Paper Mode:** Test strategies using **Live Market Data** but with simulated capital before going real.
* ♻️ **Crash Recovery:** SQLite-backed state management ensures the bot remembers active trades even after a power failure or restart.

---

### 3. Feature List (For the Features section)

* **Exchange:** NSE Futures & Options (NIFTY 50).
* **Broker:** Upstox (V2 API).
* **Strategy:** Automated Intraday Option Buying (Momentum/Breakout).
* **Risk Management:** Hard Stop Loss, Dynamic Trailing, and Target Profit locking.
* **Architecture:** Multi-threaded Python (Strategy Engine + Telegram Service).
* **Deployment:** Ready for AWS EC2 (includes Systemd configuration).
* **Database:** Local SQLite for persistent trade history and configuration.
