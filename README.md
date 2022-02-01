# TC-08 Python Logger
TC-08 thermocouple data logger python implementation with notification mailing capability.

# Requirements
- pyqtgraph
- [picosdk-python-wrappers](https://github.com/picotech/picosdk-python-wrappers)
- [TC-08 PicoSDK](vhttps://www.picotech.com/downloads)

# SMTP Configuration
Create a `smtp_config.txt` at the same directory as the scripts with the SMTP configuration. For instance, using Gmail as the SMTP server requires
```
host:smtp.gmail.com
port:587
user:me@gmail.com
password:12345
```

# Usage
## Command line
```bash
python tc08.py
```

## Windows with Anaconda
Double click `run_tc08.vbs` to launch the software. If failed, check and edit the paths in `run_tc08.bat`.
