/* The same as nbody.eng: the n-body simulation from the Computer Language Benchmarks Game. */
#include <math.h>
#include <stdio.h>

#define PI 3.141592653589793
#define SOLAR_MASS (4 * PI * PI)
#define DAYS 365.24

typedef struct { double x, y, z, vx, vy, vz, mass; } Body;

static Body bodies[5] = {
    {0, 0, 0, 0, 0, 0, SOLAR_MASS},
    {4.84143144246472090, -1.16032004402742839, -0.103622044471123109, 0.00166007664274403694 * DAYS,
     0.00769901118419740425 * DAYS, -0.0000690460016972063023 * DAYS, 0.000954791938424326609 * SOLAR_MASS},
    {8.34336671824457987, 4.12479856412430479, -0.403523417114321381, -0.00276742510726862411 * DAYS,
     0.00499852801234917238 * DAYS, 0.0000230417297573763929 * DAYS, 0.000285885980666130812 * SOLAR_MASS},
    {12.8943695621391310, -15.1111514016986312, -0.223307578892655734, 0.00296460137564761618 * DAYS,
     0.00237847173959480950 * DAYS, -0.0000296589568540237556 * DAYS, 0.0000436624404335156298 * SOLAR_MASS},
    {15.3796971148509165, -25.9193146099879641, 0.179258772950371181, 0.00268067772490389322 * DAYS,
     0.00162824170038242295 * DAYS, -0.0000951592254519715870 * DAYS, 0.0000515138902046611451 * SOLAR_MASS}};

static double energy(void) {
    double e = 0;
    for (int i = 0; i < 5; i++) {
        Body *b = &bodies[i];
        e += 0.5 * b->mass * (b->vx * b->vx + b->vy * b->vy + b->vz * b->vz);
        for (int j = i + 1; j < 5; j++) {
            Body *c = &bodies[j];
            double dx = b->x - c->x, dy = b->y - c->y, dz = b->z - c->z;
            e -= b->mass * c->mass / sqrt(dx * dx + dy * dy + dz * dz);
        }
    }
    return e;
}

static void advance(double dt) {
    for (int i = 0; i < 5; i++) {
        Body *b = &bodies[i];
        for (int j = i + 1; j < 5; j++) {
            Body *c = &bodies[j];
            double dx = b->x - c->x, dy = b->y - c->y, dz = b->z - c->z;
            double d2 = dx * dx + dy * dy + dz * dz;
            double mag = dt / (d2 * sqrt(d2));
            double mb = b->mass * mag, mc = c->mass * mag;
            b->vx -= dx * mc, b->vy -= dy * mc, b->vz -= dz * mc;
            c->vx += dx * mb, c->vy += dy * mb, c->vz += dz * mb;
        }
    }
    for (int i = 0; i < 5; i++) {
        Body *b = &bodies[i];
        b->x += dt * b->vx, b->y += dt * b->vy, b->z += dt * b->vz;
    }
}

int main(void) {
    double px = 0, py = 0, pz = 0;
    for (int i = 0; i < 5; i++) {
        px += bodies[i].vx * bodies[i].mass;
        py += bodies[i].vy * bodies[i].mass;
        pz += bodies[i].vz * bodies[i].mass;
    }
    bodies[0].vx = -px / SOLAR_MASS, bodies[0].vy = -py / SOLAR_MASS, bodies[0].vz = -pz / SOLAR_MASS;
    printf("%.9f\n", energy());
    for (int n = 0; n < 5000000; n++) advance(0.01);
    printf("%.9f\n", energy());
    return 0;
}
