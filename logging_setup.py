import logging
from datetime import datetime
from pathlib import Path

log_dir = f"logs_{datetime.now():%Y_%m_%d_%H_%M_%S}"
Path(log_dir).mkdir(parents=True, exist_ok=True)
log_file = f"{log_dir}/log.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
for lib in ("httpx", "telegram", "telegram.ext"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logging.info(f"[БОТ] Запущен, логи в {log_file}")