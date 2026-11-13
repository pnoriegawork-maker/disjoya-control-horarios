"""Aviso de privacidad y autorización de tratamiento (Ley 1581 de 2012, Decreto 1377 de 2013
compilado en el Decreto 1074 de 2015). El hash SHA-256 del texto exacto aceptado queda
registrado con cada consentimiento como prueba de la autorización previa, expresa e informada."""
from .security import sha256_hex

VERSION_POLITICA = "2026.11"

TEXTO_POLITICA = """AUTORIZACIÓN PARA EL TRATAMIENTO DE DATOS PERSONALES - CONTROL DE JORNADA
Responsable: el EMPLEADOR (razón social, NIT, domicilio y canal de atención publicados en la Política de Tratamiento).
Datos tratados: identificación, cargo, salario, turnos, marcaciones de asistencia (fecha, hora legal colombiana),
coordenadas GPS capturadas ÚNICAMENTE en el instante de la marcación, identificador seudonimizado del dispositivo,
dirección IP y registros de solicitudes/aprobaciones de trabajo suplementario.
Finalidades: (i) control de la jornada laboral y del trabajo suplementario (Arts. 158 a 167A CST, Art. 22 Ley 50 de 1990,
Ley 2101 de 2021); (ii) liquidación de salarios, recargos y nómina electrónica; (iii) prevención de fraude en el registro
de asistencia; (iv) atención de requerimientos del Ministerio del Trabajo, la UGPP, la DIAN y autoridades judiciales.
NO se realiza rastreo continuo de ubicación ni tratamiento de datos biométricos.
Derechos del titular (Art. 8 Ley 1581 de 2012): conocer, actualizar, rectificar, solicitar prueba de la autorización,
ser informado del uso, presentar quejas ante la SIC, revocar la autorización y/o solicitar la supresión cuando no exista
deber legal o contractual de conservar el dato. Consultas: 10 días hábiles; reclamos: 15 días hábiles (Arts. 14 y 15).
La autorización de geolocalización es facultativa: si no se otorga o se revoca, el titular dispone de un medio alterno
de marcación asistida en el centro de trabajo, sin consecuencias adversas.
Conservación: los registros se conservan durante la relación laboral y por el término de prescripción de las acciones
laborales y de los deberes de conservación documental aplicables; las coordenadas GPS se suprimen al vencer dicho término.
"""

HASH_POLITICA = sha256_hex(TEXTO_POLITICA)

