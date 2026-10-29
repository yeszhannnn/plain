# The same as nbody.eng, written plainly in Python.
import math

SOLAR_MASS = 4 * math.pi * math.pi
DAYS = 365.24


class Body:
    def __init__(self, x, y, z, vx, vy, vz, mass):
        self.x, self.y, self.z = x, y, z
        self.vx, self.vy, self.vz = vx * DAYS, vy * DAYS, vz * DAYS
        self.mass = mass * SOLAR_MASS


bodies = [
    Body(0, 0, 0, 0, 0, 0, 1),
    Body(4.84143144246472090, -1.16032004402742839, -0.103622044471123109, 0.00166007664274403694,
         0.00769901118419740425, -0.0000690460016972063023, 0.000954791938424326609),
    Body(8.34336671824457987, 4.12479856412430479, -0.403523417114321381, -0.00276742510726862411,
         0.00499852801234917238, 0.0000230417297573763929, 0.000285885980666130812),
    Body(12.8943695621391310, -15.1111514016986312, -0.223307578892655734, 0.00296460137564761618,
         0.00237847173959480950, -0.0000296589568540237556, 0.0000436624404335156298),
    Body(15.3796971148509165, -25.9193146099879641, 0.179258772950371181, 0.00268067772490389322,
         0.00162824170038242295, -0.0000951592254519715870, 0.0000515138902046611451),
]


def energy(bodies):
    e = 0.0
    for i, b in enumerate(bodies):
        e += 0.5 * b.mass * (b.vx * b.vx + b.vy * b.vy + b.vz * b.vz)
        for c in bodies[i + 1:]:
            dx, dy, dz = b.x - c.x, b.y - c.y, b.z - c.z
            e -= b.mass * c.mass / math.sqrt(dx * dx + dy * dy + dz * dz)
    return e


def advance(bodies, dt):
    for i, b in enumerate(bodies):
        for c in bodies[i + 1:]:
            dx, dy, dz = b.x - c.x, b.y - c.y, b.z - c.z
            d2 = dx * dx + dy * dy + dz * dz
            mag = dt / (d2 * math.sqrt(d2))
            mb, mc = b.mass * mag, c.mass * mag
            b.vx -= dx * mc
            b.vy -= dy * mc
            b.vz -= dz * mc
            c.vx += dx * mb
            c.vy += dy * mb
            c.vz += dz * mb
    for b in bodies:
        b.x += dt * b.vx
        b.y += dt * b.vy
        b.z += dt * b.vz


px = sum(b.vx * b.mass for b in bodies)
py = sum(b.vy * b.mass for b in bodies)
pz = sum(b.vz * b.mass for b in bodies)
bodies[0].vx, bodies[0].vy, bodies[0].vz = -px / SOLAR_MASS, -py / SOLAR_MASS, -pz / SOLAR_MASS
print(f"{energy(bodies):.9f}")
for _ in range(5000000):
    advance(bodies, 0.01)
print(f"{energy(bodies):.9f}")
