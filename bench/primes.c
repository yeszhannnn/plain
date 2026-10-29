/* The same algorithm as primes.eng, in C. */
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    long limit = 5000000, count = 0;
    char *crossed = calloc(limit + 1, 1);
    for (long n = 2; n <= limit; n++)
        if (!crossed[n]) {
            count++;
            for (long m = n * n; m <= limit; m += n) crossed[m] = 1;
        }
    printf("%ld\n", count);
    return 0;
}
