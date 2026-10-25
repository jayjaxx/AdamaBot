import datetime
import logging
from pathlib import Path

LOGS_DIR = Path("logs")

LOGGING_LEVEL = logging.INFO
LOGGING_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOGGING_DATEFMT = "%Y/%m/%d %H:%M:%S"


def setup_logging():
	LOGS_DIR.mkdir(exist_ok=True)
	now = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
	filename = LOGS_DIR / f"adama_{now}.log"

	logging.basicConfig(
		filename=filename,
		level=LOGGING_LEVEL,
		format=LOGGING_FORMAT,
		datefmt=LOGGING_DATEFMT,
	)

	root = logging.getLogger()
	root.addHandler(logging.StreamHandler())
