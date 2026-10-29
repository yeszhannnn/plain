/* Start-up, errors, printing, numbers, input and command-line arguments. */
#include <gc.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "plain_rt.h"

int pl_line;
const char *pl_file = "";

static int g_argc;
static char **g_argv;
static unsigned long long rng_state;

void pl_init(int argc, char **argv) {
    GC_INIT();
    g_argc = argc;
    g_argv = argv;
    rng_state = (unsigned long long)time(NULL) ^ ((unsigned long long)getpid() << 32) ^ 0x9E3779B97F4A7C15ULL;
}

void pl_exit(void) {
    fflush(stdout);
    exit(0);
}

void pl_fail(const char *fmt, ...) {
    fflush(stdout);
    fprintf(stderr, "%s, line %d:\n", pl_file, pl_line);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(1);
}

void *pl_alloc(size_t n) { return GC_MALLOC(n ? n : 1); }
char *pl_alloc_text(size_t n) { return GC_MALLOC_ATOMIC(n ? n : 1); }
const char *nz(const char *s) { return s ? s : ""; }

char *pl_strdup(const char *s) {
    s = nz(s);
    size_t n = strlen(s);
    char *p = pl_alloc_text(n + 1);
    memcpy(p, s, n + 1);
    return p;
}

/* ── growable text ── */

void buf_put(Buf *b, const char *s, size_t n) {
    if (b->n + n + 1 > b->cap) {
        size_t cap = (b->n + n + 1) * 2;
        char *p = pl_alloc_text(cap);
        if (b->p) memcpy(p, b->p, b->n);
        b->p = p;
        b->cap = cap;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

void buf_putc(Buf *b, char c) { buf_put(b, &c, 1); }
void buf_puts(Buf *b, const char *s) { buf_put(b, nz(s), strlen(nz(s))); }

char *buf_take(Buf *b) {
    char *p = b->p ? b->p : pl_strdup("");
    b->p = NULL;
    b->n = b->cap = 0;
    return p;
}

static int is_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

/* parts of a line are joined with one space, except around spaces and before punctuation */
void buf_piece(Buf *b, const char *s) {
    if (!s || !*s) return;
    if (b->n && !is_space(b->p[b->n - 1]) && !is_space(s[0]) && !strchr(".,!?;:)", s[0])) buf_putc(b, ' ');
    buf_puts(b, s);
}

/* ── formatting values ── */

void pl_format_float(char *out, size_t size, double x) {
    snprintf(out, size, "%.12g", x);
    if (!strpbrk(out, ".eni")) strncat(out, ".0", size - strlen(out) - 1);
}

const char *pl_skip_desc(const char *d) {
    if (*d == 'L') return pl_skip_desc(d + 1);
    if (*d == 'M') return pl_skip_desc(pl_skip_desc(d + 1));
    return d + 1;
}

const char *pl_format(Buf *b, int64_t v, const char *d) {
    char tmp[64];
    double x;
    switch (*d) {
    case 'i':
        snprintf(tmp, sizeof tmp, "%lld", (long long)v);
        buf_puts(b, tmp);
        return d + 1;
    case 'f':
        memcpy(&x, &v, sizeof x);
        pl_format_float(tmp, sizeof tmp, x);
        buf_puts(b, tmp);
        return d + 1;
    case 's':
        buf_puts(b, (const char *)(intptr_t)v);
        return d + 1;
    case 'b':
        buf_puts(b, v ? "true" : "false");
        return d + 1;
    case 'L': {
        List *l = (List *)(intptr_t)v;
        if (!l || l->len == 0) buf_puts(b, "nothing");
        for (int64_t i = 0; l && i < l->len; i++) {
            if (i) buf_puts(b, i == l->len - 1 ? " and " : ", ");
            pl_format(b, l->items[i], d + 1);
        }
        return pl_skip_desc(d);
    }
    default: { /* 'M' */
        Map *m = (Map *)(intptr_t)v;
        const char *kd = d + 1, *vd = pl_skip_desc(kd);
        List *keys = m ? pl_map_keys(m) : NULL;
        if (!keys || keys->len == 0) buf_puts(b, "nothing");
        for (int64_t i = 0; keys && i < keys->len; i++) {
            if (i) buf_puts(b, ", ");
            pl_format(b, keys->items[i], kd);
            buf_puts(b, ": ");
            pl_format(b, pl_map_value_at(m, keys->items[i]), vd);
        }
        return pl_skip_desc(d);
    }
    }
}

char *pl_to_text(int64_t v, const char *desc) {
    Buf b = {0};
    pl_format(&b, v, desc);
    return buf_take(&b);
}

static Buf out, cat;   /* cat: text being glued for "and then" values */

static void piece_value(Buf *b, int64_t v, const char *desc) {
    Buf tmp = {0};
    pl_format(&tmp, v, desc);
    buf_piece(b, tmp.p);
}

void pl_out_text(const char *s) { buf_piece(&out, s); }
void pl_out_value(int64_t v, const char *desc) { piece_value(&out, v, desc); }

void pl_out_int(int64_t v) {
    char tmp[32];
    snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    buf_piece(&out, tmp);
}

void pl_out_float(double x) {
    char tmp[64];
    pl_format_float(tmp, sizeof tmp, x);
    buf_piece(&out, tmp);
}

void pl_out_print(void) {
    fwrite(out.p ? out.p : "", 1, out.n, stdout);
    fputc('\n', stdout);
    out.n = 0;
    if (out.p) out.p[0] = 0;
}

/* the text of the line being built, for replies; the buffer is reset */
char *pl_out_take(void) {
    char *s = pl_strdup(out.p ? out.p : "");
    out.n = 0;
    if (out.p) out.p[0] = 0;
    return s;
}

void pl_cat_begin(void) {
    cat.n = 0;
    if (cat.p) cat.p[0] = 0;
}

void pl_cat_text(const char *s) { buf_piece(&cat, s); }
void pl_cat_value(int64_t v, const char *desc) { piece_value(&cat, v, desc); }

void pl_cat_int(int64_t v) {
    char tmp[32];
    snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    buf_piece(&cat, tmp);
}

void pl_cat_float(double x) {
    char tmp[64];
    pl_format_float(tmp, sizeof tmp, x);
    buf_piece(&cat, tmp);
}

char *pl_cat_end(void) { return pl_strdup(cat.p ? cat.p : ""); }

/* ── slots ── */

int pl_slot_equal(int64_t a, int64_t b, char kind) {
    if (kind == 'f') {
        double x, y;
        memcpy(&x, &a, sizeof x);
        memcpy(&y, &b, sizeof y);
        return x == y;
    }
    if (kind == 's') return strcmp(nz((const char *)(intptr_t)a), nz((const char *)(intptr_t)b)) == 0;
    return a == b;
}

int pl_slot_compare(int64_t a, int64_t b, char kind) {
    if (kind == 'f') {
        double x, y;
        memcpy(&x, &a, sizeof x);
        memcpy(&y, &b, sizeof y);
        return (x > y) - (x < y);
    }
    if (kind == 's') return strcmp(nz((const char *)(intptr_t)a), nz((const char *)(intptr_t)b));
    return (a > b) - (a < b);
}

int64_t pl_zero_slot(const char *desc) {
    switch (*desc) {
    case 's': return (int64_t)(intptr_t)pl_strdup("");
    case 'L': return (int64_t)(intptr_t)pl_list_new();
    case 'M': return (int64_t)(intptr_t)pl_map_new(desc[1] == 's');
    default: return 0; /* 0, 0.0 and false are all zero bits */
    }
}

/* ── numbers ── */

int64_t pl_div(int64_t a, int64_t b) {
    if (b == 0) pl_fail("Division by zero.");
    if (b == -1) return (int64_t)(0 - (uint64_t)a);
    return a / b;
}

int64_t pl_rem(int64_t a, int64_t b) {
    if (b == 0) pl_fail("Division by zero.");
    if (b == -1) return 0;
    return a % b;
}

int64_t pl_ipow(int64_t a, int64_t b) {
    if (b < 0) pl_fail("A whole number to a negative power is a fraction. Use a decimal, for example 2.0.");
    uint64_t r = 1, x = (uint64_t)a;
    while (b) {
        if (b & 1) r *= x;
        x *= x;
        b >>= 1;
    }
    return (int64_t)r;
}

double pl_fdiv(double a, double b) {
    if (b == 0) pl_fail("Division by zero.");
    return a / b;
}

double pl_fmod(double a, double b) {
    if (b == 0) pl_fail("Division by zero.");
    return fmod(a, b);
}

double pl_fpow(double a, double b) { return pow(a, b); }

double pl_sqrt(double x) {
    if (x < 0) pl_fail("I can't take the square root of a negative number.");
    return sqrt(x);
}

int64_t pl_round(double x) { return (int64_t)llround(x); }
int64_t pl_floor(double x) { return (int64_t)floor(x); }
int64_t pl_ceil(double x) { return (int64_t)ceil(x); }

/* "  -42 " is a whole number; up to 18 digits */
int pl_match_int(const char *s, int64_t *out) {
    s = nz(s);
    while (is_space(*s)) s++;
    const char *start = s;
    if (*s == '+' || *s == '-') s++;
    const char *digits = s;
    while (*s >= '0' && *s <= '9') s++;
    size_t n = s - digits;
    while (is_space(*s)) s++;
    if (*s || n == 0 || n > 18) return 0;
    if (out) *out = strtoll(start, NULL, 10);
    return 1;
}

/* "3", "-2.5"; digits are required on both sides of the point */
int pl_match_decimal(const char *s, double *out) {
    s = nz(s);
    while (is_space(*s)) s++;
    const char *start = s;
    if (*s == '+' || *s == '-') s++;
    const char *digits = s;
    while (*s >= '0' && *s <= '9') s++;
    if (s == digits) return 0;
    if (*s == '.') {
        const char *frac = ++s;
        while (*s >= '0' && *s <= '9') s++;
        if (s == frac) return 0;
    }
    const char *end = s;
    while (is_space(*s)) s++;
    if (*s) return 0;
    if (out) {
        char tmp[400];
        size_t n = end - start < 399 ? (size_t)(end - start) : 399;
        memcpy(tmp, start, n);
        tmp[n] = 0;
        *out = strtod(tmp, NULL);
    }
    return 1;
}

int64_t pl_text_to_int(const char *s) {
    int64_t v;
    if (!pl_match_int(s, &v)) pl_fail("\"%s\" is not a whole number.", nz(s));
    return v;
}

double pl_text_to_float(const char *s) {
    double v;
    if (!pl_match_decimal(s, &v)) pl_fail("\"%s\" is not a number.", nz(s));
    return v;
}

int32_t pl_text_is_number(const char *s) { return pl_match_decimal(s, NULL); }

int64_t pl_random(int64_t lo, int64_t hi) {
    if (hi < lo) {
        int64_t t = lo;
        lo = hi;
        hi = t;
    }
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    unsigned long long range = (unsigned long long)(hi - lo) + 1;
    return lo + (int64_t)(range ? rng_state % range : rng_state);
}

/* ── input ── */

static char *read_answer(const char *prompt) {
    prompt = nz(prompt);
    size_t n = strlen(prompt);
    fputs(prompt, stdout);
    if (n && prompt[n - 1] != ' ' && prompt[n - 1] != '\n') fputc(' ', stdout);
    fflush(stdout);
    Buf b = {0};
    int c;
    while ((c = getchar()) != EOF && c != '\n') buf_putc(&b, (char)c);
    if (c == EOF && b.n == 0) pl_fail("There is no more input to read.");
    if (b.n && b.p[b.n - 1] == '\r') b.p[--b.n] = 0;
    return buf_take(&b);
}

char *pl_ask_text(const char *prompt) { return read_answer(prompt); }

int64_t pl_ask_int(const char *prompt) {
    int64_t v;
    for (;;) {
        if (pl_match_int(read_answer(prompt), &v)) return v;
        fputs("Please type a whole number.\n", stdout);
    }
}

double pl_ask_float(const char *prompt) {
    double v;
    for (;;) {
        if (pl_match_decimal(read_answer(prompt), &v)) return v;
        fputs("Please type a number.\n", stdout);
    }
}

List *pl_arguments(void) {
    List *l = pl_list_new();
    for (int i = 1; i < g_argc; i++) pl_list_add(l, (int64_t)(intptr_t)pl_strdup(g_argv[i]));
    return l;
}
