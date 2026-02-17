"""
logger.py - Sistema de logging para terminal + arquivo simultaneo
Logs salvos em Logs_secoes/logs_YYYY-MM-DD.txt (mesmo dia = append, novo dia = novo arquivo)
"""
import logging
import os
import sys
from datetime import datetime

# Diretorio de logs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "Logs_secoes")
os.makedirs(LOGS_DIR, exist_ok=True)


class _LoggerSetup:
    """Singleton para configurar o logger uma unica vez."""
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def setup(self) -> logging.Logger:
        if self._initialized:
            return logging.getLogger("restaurant_bi")

        logger = logging.getLogger("restaurant_bi")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        # Formato: timestamp + mensagem (sem level redundante, os prefixos [TAG] ja indicam)
        fmt = logging.Formatter(
            "%(asctime)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Handler: terminal (stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)

        # Handler: arquivo do dia
        log_file = os.path.join(
            LOGS_DIR, f"logs_{datetime.now().strftime('%Y-%m-%d')}.txt"
        )
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

        self._initialized = True

        logger.info("=" * 60)
        logger.info(f"  SESSAO INICIADA: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        return logger


def get_logger() -> logging.Logger:
    """Retorna o logger configurado (terminal + arquivo)."""
    return _LoggerSetup().setup()


# Atalho global
log = get_logger()
