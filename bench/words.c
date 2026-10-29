/* The same as words.eng: a small open-addressing hash table from word to count. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SIZE 32768

static char *keys[SIZE];
static long counts[SIZE];

static uint64_t hash(const char *s) {
    uint64_t h = 1469598103934665603ull;
    while (*s) h = (h ^ (unsigned char)*s++) * 1099511628211ull;
    return h;
}

static long *slot(char *word) {
    uint64_t i = hash(word) & (SIZE - 1);
    while (keys[i] && strcmp(keys[i], word)) i = (i + 1) & (SIZE - 1);
    if (!keys[i]) keys[i] = word;
    return &counts[i];
}

int main(void) {
    char *vocabulary[10000];
    for (int n = 0; n < 10000; n++) {
        vocabulary[n] = malloc(16);
        snprintf(vocabulary[n], 16, "w %d", n + 1);
    }
    long seed = 1, different = 0;
    for (long n = 0; n < 5000000; n++) {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        long *c = slot(vocabulary[seed % 10000]);
        if (!*c) different++;
        (*c)++;
    }
    printf("%ld %ld\n", different, *slot("w 1"));
    return 0;
}
