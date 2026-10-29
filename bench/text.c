#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    size_t len = 0, cap = 16;
    char *built = malloc(cap);
    for (long n = 1; n <= 1000000; n++) {
        char tmp[24];
        int k = snprintf(tmp, sizeof tmp, "%ld", n);
        if (len + k + 1 > cap) built = realloc(built, cap *= 2);
        memcpy(built + len, tmp, k + 1);
        len += k;
    }
    printf("%zu\n", strlen(built));
    return 0;
}
