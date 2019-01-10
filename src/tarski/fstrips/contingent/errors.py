# -*- coding: utf-8 -*-
from ...errors import TarskiError, DuplicateDefinition, UndefinedElement


class ObservationExpressivenessMismatch(TarskiError):
    def __init__(self, sensor, msg=None):
        msg = 'mismatch between the expressiveness of the specified sensor and the one supported by the language!'
        super().__init__(
            'Sensing action: {}: {}\nObservation should be literal (Atom, or CompoundFormula) and it is: {}'.format(
                sensor.name, msg, type(sensor.obs)))


class DuplicateSensorDefinition(DuplicateDefinition):
    pass


class UndefinedSensor(UndefinedElement):
    pass
