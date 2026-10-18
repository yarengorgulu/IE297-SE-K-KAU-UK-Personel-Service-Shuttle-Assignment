"""Uzun süren arayüz görevlerini Qt iş parçacığı havuzunda çalıştırır."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class ProgressWorkerSignals(WorkerSignals):
    progress = Signal(int, int)


class FunctionWorker(QRunnable):
    """Verilen çağrılabilir işi ana arayüz iş parçacığı dışında çalıştırır."""

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as error:
            logger.exception("Arka plan işlemi başarısız oldu.")
            message = str(error).strip() or "İşlem sırasında beklenmeyen bir hata oluştu."
            self.signals.error.emit(message)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class ProgressFunctionWorker(QRunnable):
    """İlerleme bildiren ve kayıtlar arasında iptal edilebilen arka plan görevi."""

    def __init__(
        self,
        function: Callable[[Callable[[int, int], None], Callable[[], bool]], Any],
    ) -> None:
        super().__init__()
        self.function = function
        self.signals = ProgressWorkerSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(
                self.signals.progress.emit,
                self._cancelled.is_set,
            )
        except Exception as error:
            logger.exception("İlerlemeli arka plan işlemi başarısız oldu.")
            message = str(error).strip() or "İşlem sırasında beklenmeyen bir hata oluştu."
            self.signals.error.emit(message)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
