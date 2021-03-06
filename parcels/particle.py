from parcels.kernels.error import ErrorCode
from operator import attrgetter
import numpy as np


__all__ = ['ScipyParticle', 'JITParticle', 'Variable']


lastID = 0  # module-level variable keeping track of last Particle ID used


class Variable(object):
    """Descriptor class that delegates data access to particle data

    :param name: Variable name as used within kernels
    :param dtype: Data type (numpy.dtype) of the variable
    :param initial: Initial value of the variable
    :param to_write: Boolean to control whether Variable is written to NetCDF file
    """
    def __init__(self, name, dtype=np.float32, initial=0, to_write=True):
        self.name = name
        self.dtype = dtype
        self.initial = initial
        self.to_write = to_write

    def __get__(self, instance, cls):
        if instance is None:
            return self
        if issubclass(cls, JITParticle):
            return instance._cptr.__getitem__(self.name)
        else:
            return getattr(instance, "_%s" % self.name, self.initial)

    def __set__(self, instance, value):
        if isinstance(instance, JITParticle):
            instance._cptr.__setitem__(self.name, value)
        else:
            setattr(instance, "_%s" % self.name, value)

    def __repr__(self):
        return "PVar<%s|%s>" % (self.name, self.dtype)

    def is64bit(self):
        """Check whether variable is 64-bit"""
        return True if self.dtype == np.float64 or self.dtype == np.int64 else False


class ParticleType(object):
    """Class encapsulating the type information for custom particles

    :param user_vars: Optional list of (name, dtype) tuples for custom variables
    """

    def __init__(self, pclass):
        if not isinstance(pclass, type):
            raise TypeError("Class object required to derive ParticleType")
        if not issubclass(pclass, ScipyParticle):
            raise TypeError("Class object does not inherit from parcels.ScipyParticle")

        self.name = pclass.__name__
        self.uses_jit = issubclass(pclass, JITParticle)
        # Pick Variable objects out of __dict__. First pick all the 64-bit ones so that
        # they are aligned for the JIT cptr
        self.variables = [v for v in pclass.__dict__.values() if isinstance(v, Variable) and v.is64bit()] + \
                         [v for v in pclass.__dict__.values() if isinstance(v, Variable) and not v.is64bit()]
        for cls in pclass.__bases__:
            if issubclass(cls, ScipyParticle):
                # Add inherited particle variables
                ptype = cls.getPType()
                self.variables = ptype.variables + self.variables

    def __repr__(self):
        return "PType<%s>::%s" % (self.name, self.variables)

    @property
    def _cache_key(self):
        return "-".join(["%s:%s" % (v.name, v.dtype) for v in self.variables])

    @property
    def dtype(self):
        """Numpy.dtype object that defines the C struct"""
        type_list = [(v.name, v.dtype) for v in self.variables]
        if self.size % 8 > 0:
            # Add padding to be 64-bit aligned
            type_list += [('pad', np.float32)]
        return np.dtype(type_list)

    @property
    def size(self):
        """Size of the underlying particle struct in bytes"""
        return sum([8 if v.is64bit() else 4 for v in self.variables])


class _Particle(object):
    """Private base class for all particle types"""

    def __init__(self):
        ptype = self.getPType()
        # Explicit initialiastion of all particle variables
        for v in ptype.variables:
            if isinstance(v.initial, attrgetter):
                initial = v.initial(self)
            else:
                initial = v.initial
            # Enforce type of initial value
            setattr(self, v.name, v.dtype(initial))

        # Placeholder for explicit error handling
        self.exception = None

    @classmethod
    def getPType(cls):
        return ParticleType(cls)


class ScipyParticle(_Particle):
    """Class encapsulating the basic attributes of a particle,
    to be executed in SciPy mode

    :param lon: Initial longitude of particle
    :param lat: Initial latitude of particle
    :param grid: :mod:`parcels.grid.Grid` object to track this particle on
    :param dt: Execution timestep for this particle
    :param time: Current time of the particle

    Additional Variables can be added via the :Class Variable: objects
    """

    lon = Variable('lon', dtype=np.float32)
    lat = Variable('lat', dtype=np.float32)
    time = Variable('time', dtype=np.float64)
    id = Variable('id', dtype=np.int32)
    dt = Variable('dt', dtype=np.float32, to_write=False)
    state = Variable('state', dtype=np.int32, initial=ErrorCode.Success, to_write=False)

    def __init__(self, lon, lat, grid, dt=1., time=0., cptr=None):
        global lastID

        # Enforce default values through Variable descriptor
        type(self).lon.initial = lon
        type(self).lat.initial = lat
        type(self).time.initial = time
        type(self).id.initial = lastID
        lastID += 1
        type(self).dt.initial = dt
        super(ScipyParticle, self).__init__()

    def __repr__(self):
        return "P(%f, %f, %f)" % (self.lon, self.lat, self.time)

    def delete(self):
        self.state = ErrorCode.Delete


class JITParticle(ScipyParticle):
    """Particle class for JIT-based (Just-In-Time) Particle objects

    :param lon: Initial longitude of particle
    :param lat: Initial latitude of particle
    :param grid: :mod:`parcels.grid.Grid` object to track this particle on
    :param dt: Execution timestep for this particle
    :param time: Current time of the particle

    Additional Variables can be added via the :Class Variable: objects

    Users should use JITParticles for faster advection computation.

    """

    xi = Variable('xi', dtype=np.int32, to_write=False)
    yi = Variable('yi', dtype=np.int32, to_write=False)

    def __init__(self, *args, **kwargs):
        self._cptr = kwargs.pop('cptr', None)
        ptype = self.getPType()
        if self._cptr is None:
            # Allocate data for a single particle
            self._cptr = np.empty(1, dtype=ptype.dtype)[0]
        super(JITParticle, self).__init__(*args, **kwargs)

        grid = kwargs.get('grid')
        self.xi = np.where(self.lon >= grid.U.lon)[0][-1]
        self.yi = np.where(self.lat >= grid.U.lat)[0][-1]

    def __repr__(self):
        return "P(%f, %f, %f)[%d, %d]" % (self.lon, self.lat, self.time,
                                          self.xi, self.yi)
