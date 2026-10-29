/* Texts: UTF-8, letter case for Latin, Cyrillic and Kazakh letters, searching, splitting, joining. */
#include <stdio.h>
#include <string.h>

#include "plain_rt.h"

/* ── UTF-8 ── */

static uint32_t decode(const char **ps) {
    const unsigned char *s = (const unsigned char *)*ps;
    uint32_t c = s[0];
    int n = c < 0x80 ? 1 : c < 0xE0 ? 2 : c < 0xF0 ? 3 : 4;
    if (n > 1) {
        c &= 0xFF >> (n + 1);
        for (int i = 1; i < n; i++) {
            if ((s[i] & 0xC0) != 0x80) {
                *ps += 1;
                return 0xFFFD;
            }
            c = (c << 6) | (s[i] & 0x3F);
        }
    }
    *ps += n;
    return c;
}

static void encode(Buf *b, uint32_t cp) {
    if (cp < 0x80) buf_putc(b, (char)cp);
    else if (cp < 0x800) buf_putc(b, (char)(0xC0 | cp >> 6)), buf_putc(b, (char)(0x80 | (cp & 63)));
    else if (cp < 0x10000)
        buf_putc(b, (char)(0xE0 | cp >> 12)), buf_putc(b, (char)(0x80 | (cp >> 6 & 63))),
            buf_putc(b, (char)(0x80 | (cp & 63)));
    else
        buf_putc(b, (char)(0xF0 | cp >> 18)), buf_putc(b, (char)(0x80 | (cp >> 12 & 63))),
            buf_putc(b, (char)(0x80 | (cp >> 6 & 63))), buf_putc(b, (char)(0x80 | (cp & 63)));
}

/* Upper -> lower pairs beyond the regular ranges: the extra Kazakh letters. Same table as plainc/values.py. */
static const uint32_t kazakh[][2] = {{0x492, 0x493}, {0x49A, 0x49B}, {0x4A2, 0x4A3}, {0x4AE, 0x4AF},
                                     {0x4B0, 0x4B1}, {0x4BA, 0x4BB}, {0x4D8, 0x4D9}, {0x4E8, 0x4E9}};

static uint32_t to_lower(uint32_t c) {
    if (c >= 0x41 && c <= 0x5A) return c + 32;
    if (c >= 0xC0 && c <= 0xDE && c != 0xD7) return c + 32;
    if (c >= 0x410 && c <= 0x42F) return c + 32;
    if (c >= 0x400 && c <= 0x40F) return c + 80;
    for (size_t i = 0; i < sizeof kazakh / sizeof kazakh[0]; i++)
        if (c == kazakh[i][0]) return kazakh[i][1];
    return c;
}

static uint32_t to_upper(uint32_t c) {
    if (c >= 0x61 && c <= 0x7A) return c - 32;
    if (c >= 0xE0 && c <= 0xFE && c != 0xF7) return c - 32;
    if (c >= 0x430 && c <= 0x44F) return c - 32;
    if (c >= 0x450 && c <= 0x45F) return c - 80;
    for (size_t i = 0; i < sizeof kazakh / sizeof kazakh[0]; i++)
        if (c == kazakh[i][1]) return kazakh[i][0];
    return c;
}

static char *map_case(const char *s, uint32_t (*f)(uint32_t)) {
    Buf b = {0};
    s = nz(s);
    while (*s) encode(&b, f(decode(&s)));
    return buf_take(&b);
}

char *pl_lower(const char *s) { return map_case(s, to_lower); }
char *pl_upper(const char *s) { return map_case(s, to_upper); }

int64_t pl_text_len(const char *s) {
    int64_t n = 0;
    s = nz(s);
    while (*s) decode(&s), n++;
    return n;
}

static List *letters(const char *s) {
    List *l = pl_list_new();
    s = nz(s);
    while (*s) {
        const char *start = s;
        decode(&s);
        char *p = pl_alloc_text(s - start + 1);
        memcpy(p, start, s - start);
        p[s - start] = 0;
        pl_list_add(l, (int64_t)(intptr_t)p);
    }
    return l;
}

List *pl_letters(const char *s) { return letters(s); }

char *pl_text_char(const char *s, int64_t i) {
    List *l = letters(s);
    if (i == -1) {
        if (!l->len) pl_fail("There is no last letter: the text is empty.");
        i = l->len;
    }
    if (i < 1 || i > l->len)
        pl_fail("There is no letter %lld: the text has %lld letter%s.", (long long)i, (long long)l->len,
                l->len == 1 ? "" : "s");
    return (char *)(intptr_t)l->items[i - 1];
}

char *pl_text_slice(const char *s, int64_t a, int64_t b) {
    List *l = letters(s);
    if (a < 1) a = 1;
    if (b > l->len) b = l->len;
    Buf out = {0};
    for (int64_t i = a; i <= b; i++) buf_puts(&out, (const char *)(intptr_t)l->items[i - 1]);
    return buf_take(&out);
}

/* ── comparing and searching ── */

int32_t pl_text_cmp(const char *a, const char *b) { return strcmp(nz(a), nz(b)); }

static int is_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

char *pl_trim(const char *s) {
    s = nz(s);
    while (is_space(*s)) s++;
    size_t n = strlen(s);
    while (n && is_space(s[n - 1])) n--;
    char *p = pl_alloc_text(n + 1);
    memcpy(p, s, n);
    p[n] = 0;
    return p;
}

/* equal ignoring letter case and surrounding spaces: how "when someone sends" matches */
int32_t pl_text_same(const char *a, const char *b) { return strcmp(pl_lower(pl_trim(a)), pl_lower(pl_trim(b))) == 0; }

int32_t pl_contains(const char *h, const char *n) { return strstr(pl_lower(h), pl_lower(n)) != NULL; }

int32_t pl_starts(const char *h, const char *n) {
    char *ln = pl_lower(n);
    return strncmp(pl_lower(h), ln, strlen(ln)) == 0;
}

char *pl_text_glue(const char *a, const char *b) {
    Buf out = {0};
    buf_puts(&out, a);
    buf_puts(&out, b);
    return buf_take(&out);
}

char *pl_replace(const char *s, const char *a, const char *b) {
    s = nz(s), a = nz(a);
    size_t n = strlen(a);
    if (!n) return pl_strdup(s);
    Buf out = {0};
    const char *hit;
    while ((hit = strstr(s, a))) {
        buf_put(&out, s, hit - s);
        buf_puts(&out, b);
        s = hit + n;
    }
    buf_puts(&out, s);
    return buf_take(&out);
}

/* ── splitting and joining ── */

static void add_text(List *l, const char *s, size_t n) {
    char *p = pl_alloc_text(n + 1);
    memcpy(p, s, n);
    p[n] = 0;
    pl_list_add(l, (int64_t)(intptr_t)p);
}

List *pl_split(const char *s, const char *sep) {
    s = nz(s), sep = nz(sep);
    size_t n = strlen(sep);
    if (!n) pl_fail("I can't split by an empty text.");
    List *l = pl_list_new();
    const char *hit;
    while ((hit = strstr(s, sep))) {
        add_text(l, s, hit - s);
        s = hit + n;
    }
    add_text(l, s, strlen(s));
    return l;
}

List *pl_words(const char *s) {
    List *l = pl_list_new();
    s = nz(s);
    while (*s) {
        while (is_space(*s)) s++;
        const char *start = s;
        while (*s && !is_space(*s)) s++;
        if (s > start) add_text(l, start, s - start);
    }
    return l;
}

List *pl_lines(const char *s) {
    List *l = pl_list_new();
    s = nz(s);
    while (*s) {
        const char *end = strchr(s, '\n');
        size_t n = end ? (size_t)(end - s) : strlen(s);
        add_text(l, s, n && s[n - 1] == '\r' ? n - 1 : n);
        if (!end) break;
        s = end + 1;
    }
    return l;
}

char *pl_join(List *l, const char *desc, const char *sep) {
    Buf out = {0};
    for (int64_t i = 0; l && i < l->len; i++) {
        if (i) buf_puts(&out, sep);
        pl_format(&out, l->items[i], desc);
    }
    return buf_take(&out);
}
