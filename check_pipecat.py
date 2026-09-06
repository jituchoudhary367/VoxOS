import pipecat.services
import pkgutil

print([modname for importer, modname, ispkg in pkgutil.iter_modules(pipecat.services.__path__)])
