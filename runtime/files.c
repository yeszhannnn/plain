/* Files: read a whole file, write or add lines, check and delete. */
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "plain_rt.h"

char *pl_file_read(const char *path) {
    FILE *f = fopen(nz(path), "rb");
    if (!f) pl_fail("I can't read the file \"%s\".", nz(path));
    Buf b = {0};
    char chunk[65536];
    size_t n;
    while ((n = fread(chunk, 1, sizeof chunk, f)) > 0) buf_put(&b, chunk, n);
    fclose(f);
    return buf_take(&b);
}

void pl_file_write(const char *path, const char *text, int32_t append) {
    FILE *f = fopen(nz(path), append ? "ab" : "wb");
    if (!f) pl_fail("I can't write to the file \"%s\".", nz(path));
    text = nz(text);
    size_t n = strlen(text);
    fwrite(text, 1, n, f);
    if (!n || text[n - 1] != '\n') fputc('\n', f);
    fclose(f);
}

int32_t pl_file_exists(const char *path) {
    struct stat st;
    return stat(nz(path), &st) == 0 && S_ISREG(st.st_mode);
}

void pl_file_delete(const char *path) {
    if (pl_file_exists(path) && remove(nz(path)) != 0) pl_fail("I can't delete the file \"%s\".", nz(path));
}
