import psutil
import time
from shared.core.Logger import get_logger

logger = get_logger(__name__)

def get_server_temperature():
    """Retrieve the server's current temperature."""
    try:
        if hasattr(psutil, 'sensors_temperatures'):
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if 'coretemp' in name.lower() or 'cpu' in name.lower():
                        for entry in entries:
                            if entry.current:
                                return round(entry.current, 1)
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current:
                            return round(entry.current, 1)
                            
        import sys
        if sys.platform == 'win32':
            try:
                import wmi
                w = wmi.WMI(namespace="root\\wmi")
                temperature_info = w.MSAcpi_ThermalZoneTemperature()
                if temperature_info:
                    temp_c = (temperature_info[0].CurrentTemperature / 10.0) - 273.15
                    return round(temp_c, 1)
            except Exception as wmi_err:
                logger.debug(f"WMI temperature fetch failed: {wmi_err}")
                
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000.0
                return round(temp, 1)
        except Exception as e: 
            logger.debug(f"Could not read thermal_zone0: {e}")
    except Exception as e: 
        logger.debug(f"Could not get server temperature: {e}")
    return None

def get_cpu_percent():
    """Retrieve the server's CPU usage percentage."""
    return psutil.cpu_percent(interval=None)

def get_memory_percent():
    """Retrieve the server's Memory usage percentage."""
    memory = psutil.virtual_memory()
    return memory.percent

def get_load_average():
    """Retrieve the server's load average."""
    try:
        if hasattr(psutil, 'getloadavg'):
            return psutil.getloadavg()[0]
    except Exception as e:
        logger.debug(f"Could not get load average: {e}")
    return 0

def get_uptime():
    """Retrieve the server's uptime in seconds."""
    return time.time() - psutil.Process().create_time()
