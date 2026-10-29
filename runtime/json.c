/* JSON, just enough for the Bot API and for saving a bot's memory. Trees use malloc, not the GC. */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json.h"

static const char *jp;

void jfree(J *j) {
    if (!j) return;
    for (int k = 0; k < j->len; k++) {
        jfree(j->items[k]);
        if (j->keys) free(j->keys[k]);
    }
    free(j->items);
    free(j->keys);
    free(j->s);
    free(j);
}

static J *jnew(int type) {
    J *j = calloc(1, sizeof *j);
    j->type = type;
    return j;
}

static void jpush(J *j, char *key, J *v) {
    if (j->len == j->cap) {
        j->cap = j->cap ? j->cap * 2 : 8;
        j->items = realloc(j->items, j->cap * sizeof *j->items);
        if (j->type == J_OBJ) j->keys = realloc(j->keys, j->cap * sizeof *j->keys);
    }
    if (j->type == J_OBJ) j->keys[j->len] = key;
    j->items[j->len++] = v;
}

static void jws(void) {
    while (*jp && isspace((unsigned char)*jp)) jp++;
}

static unsigned jhex(const char *p) {
    unsigned v = 0;
    for (int k = 0; k < 4; k++) {
        int c = tolower((unsigned char)p[k]);
        v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else return 0xFFFD;
    }
    return v;
}

typedef struct {
    char *p;
    size_t n, cap;
} MBuf;

static void mput(MBuf *b, char c) {
    if (b->n + 2 > b->cap) {
        b->cap = b->cap ? b->cap * 2 : 64;
        b->p = realloc(b->p, b->cap);
    }
    b->p[b->n++] = c;
    b->p[b->n] = 0;
}

static void mutf8(MBuf *b, unsigned cp) {
    if (cp < 0x80) mput(b, cp);
    else if (cp < 0x800) mput(b, 0xC0 | cp >> 6), mput(b, 0x80 | (cp & 63));
    else if (cp < 0x10000) mput(b, 0xE0 | cp >> 12), mput(b, 0x80 | (cp >> 6 & 63)), mput(b, 0x80 | (cp & 63));
    else
        mput(b, 0xF0 | cp >> 18), mput(b, 0x80 | (cp >> 12 & 63)), mput(b, 0x80 | (cp >> 6 & 63)),
            mput(b, 0x80 | (cp & 63));
}

static char *jstr(void) {
    MBuf b = {0};
    mput(&b, 0);
    b.n = 0;
    jp++; /* opening quote */
    while (*jp && *jp != '"') {
        if (*jp != '\\' || !jp[1]) {
            mput(&b, *jp++);
            continue;
        }
        jp++;
        char e = *jp++;
        switch (e) {
        case 'n': mput(&b, '\n'); break;
        case 't': mput(&b, '\t'); break;
        case 'r': mput(&b, '\r'); break;
        case 'b': mput(&b, '\b'); break;
        case 'f': mput(&b, '\f'); break;
        case 'u': {
            if (strnlen(jp, 4) < 4) {
                jp += strlen(jp);
                break;
            }
            unsigned cp = jhex(jp);
            jp += 4;
            if (cp >= 0xD800 && cp <= 0xDBFF && jp[0] == '\\' && jp[1] == 'u' && strnlen(jp + 2, 4) == 4) {
                unsigned lo = jhex(jp + 2);
                if (lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    jp += 6;
                }
            }
            mutf8(&b, cp);
            break;
        }
        default: mput(&b, e);
        }
    }
    if (*jp == '"') jp++;
    return b.p;
}

static J *jval(void) {
    jws();
    if (*jp == '{' || *jp == '[') {
        int obj = *jp == '{';
        char close = obj ? '}' : ']';
        J *j = jnew(obj ? J_OBJ : J_ARR);
        jp++;
        jws();
        if (*jp == close) {
            jp++;
            return j;
        }
        for (;;) {
            char *key = NULL;
            jws();
            if (obj) {
                if (*jp != '"') break;
                key = jstr();
                jws();
                if (*jp != ':') {
                    free(key);
                    break;
                }
                jp++;
            }
            J *v = jval();
            if (!v) {
                free(key);
                break;
            }
            jpush(j, key, v);
            jws();
            if (*jp == ',') {
                jp++;
                continue;
            }
            if (*jp == close) {
                jp++;
                return j;
            }
            break;
        }
        jfree(j);
        return NULL;
    }
    if (*jp == '"') {
        J *j = jnew(J_STR);
        j->s = jstr();
        return j;
    }
    if (!strncmp(jp, "true", 4) || !strncmp(jp, "false", 5)) {
        J *j = jnew(J_BOOL);
        j->i = *jp == 't';
        jp += j->i ? 4 : 5;
        return j;
    }
    if (!strncmp(jp, "null", 4)) {
        jp += 4;
        return jnew(J_NULL);
    }
    if (*jp == '-' || isdigit((unsigned char)*jp)) {
        J *j = jnew(J_NUM);
        char *end;
        j->d = strtod(jp, &end);
        j->i = strtoll(jp, NULL, 10);
        for (const char *p = jp; p < end; p++)
            if (*p == '.' || *p == 'e' || *p == 'E') j->is_float = 1;
        jp = end;
        return j;
    }
    return NULL;
}

J *jparse(const char *text) {
    jp = text;
    return jval();
}

const char *jparse_end(void) { return jp; }

J *jget(J *o, const char *key) {
    if (!o || o->type != J_OBJ) return NULL;
    for (int k = 0; k < o->len; k++)
        if (!strcmp(o->keys[k], key)) return o->items[k];
    return NULL;
}

void json_text(Buf *b, const char *s) {
    buf_putc(b, '"');
    for (s = nz(s); *s; s++) {
        unsigned char c = *s;
        if (c == '"' || c == '\\') buf_putc(b, '\\'), buf_putc(b, c);
        else if (c == '\n') buf_puts(b, "\\n");
        else if (c == '\t') buf_puts(b, "\\t");
        else if (c == '\r') buf_puts(b, "\\r");
        else if (c == '\b') buf_puts(b, "\\b");
        else if (c == '\f') buf_puts(b, "\\f");
        else if (c < 0x20) {
            char tmp[8];
            snprintf(tmp, sizeof tmp, "\\u%04x", c);
            buf_puts(b, tmp);
        } else buf_putc(b, c);
    }
    buf_putc(b, '"');
}

/* the shortest decimal that reads back as the same number, as Python's json writes it */
static void json_float(Buf *b, double x) {
    char tmp[40];
    if (x != x || x - x != 0) x = 0;
    for (int digits = 1; digits <= 17; digits++) {
        snprintf(tmp, sizeof tmp, "%.*g", digits, x);
        if (strtod(tmp, NULL) == x) break;
    }
    if (!strpbrk(tmp, ".eni")) strcat(tmp, ".0");
    buf_puts(b, tmp);
}

/* field "first name" is written as "first_name" */
static void json_field(Buf *b, const char *name) {
    char *key = pl_strdup(name);
    for (char *p = key; *p; p++)
        if (*p == ' ') *p = '_';
    json_text(b, key);
}

void pl_json_write(Buf *b, int64_t v, const char *d) {
    char tmp[40];
    double x;
    switch (*d) {
    case 'i':
        snprintf(tmp, sizeof tmp, "%lld", (long long)v);
        buf_puts(b, tmp);
        break;
    case 'f':
        memcpy(&x, &v, sizeof x);
        json_float(b, x);
        break;
    case 's': json_text(b, (const char *)(intptr_t)v); break;
    case 'b': buf_puts(b, v ? "true" : "false"); break;
    case 'K': {
        const Kind *k = pl_kind(d);
        int64_t *o = (int64_t *)(intptr_t)v;
        if (!o) {
            buf_puts(b, "null");
            break;
        }
        buf_putc(b, '{');
        for (int i = 0; i < k->nfields; i++) {
            if (i) buf_puts(b, ", ");
            json_field(b, k->fields[i]);
            buf_puts(b, ": ");
            pl_json_write(b, o[i], k->descs[i]);
        }
        buf_putc(b, '}');
        break;
    }
    case 'L': {
        List *l = (List *)(intptr_t)v;
        buf_putc(b, '[');
        for (int64_t i = 0; i < pl_list_len(l); i++) {
            if (i) buf_puts(b, ", ");
            pl_json_write(b, l->items[i], d + 1);
        }
        buf_putc(b, ']');
        break;
    }
    default: {
        Map *m = (Map *)(intptr_t)v;
        const char *vd = pl_skip_desc(d + 1);
        List *keys = pl_map_keys(m);
        buf_putc(b, '{');
        for (int64_t i = 0; i < keys->len; i++) {
            if (i) buf_puts(b, ", ");
            if (d[1] == 's') json_text(b, (const char *)(intptr_t)keys->items[i]);
            else {
                snprintf(tmp, sizeof tmp, "\"%lld\"", (long long)keys->items[i]);
                buf_puts(b, tmp);
            }
            buf_puts(b, ": ");
            pl_json_write(b, pl_map_value_at(m, keys->items[i]), vd);
        }
        buf_putc(b, '}');
    }
    }
}

int64_t pl_json_read(J *j, const char *d) {
    switch (*d) {
    case 'i':
        if (j && j->type == J_NUM) return j->is_float ? (int64_t)j->d : j->i;
        return 0;
    case 'f': {
        double x = j && j->type == J_NUM ? (j->is_float ? j->d : (double)j->i) : 0.0;
        int64_t v;
        memcpy(&v, &x, sizeof v);
        return v;
    }
    case 's': return (int64_t)(intptr_t)pl_strdup(j && j->type == J_STR ? j->s : "");
    case 'b': return j && j->type == J_BOOL ? j->i : 0;
    case 'K': {
        if (!j || j->type != J_OBJ) return 0;
        const Kind *k = pl_kind(d);
        int64_t *o = pl_alloc((k->nfields ? k->nfields : 1) * sizeof(int64_t));
        for (int i = 0; i < k->nfields; i++) {
            char *key = pl_strdup(k->fields[i]);
            for (char *p = key; *p; p++)
                if (*p == ' ') *p = '_';
            J *f = jget(j, key);
            if (!f) f = jget(j, k->fields[i]);
            o[i] = pl_json_read(f, k->descs[i]);
        }
        return (int64_t)(intptr_t)o;
    }
    case 'L': {
        List *l = pl_list_of(d + 1);
        for (int k = 0; j && j->type == J_ARR && k < j->len; k++) pl_list_add(l, pl_json_read(j->items[k], d + 1));
        return (int64_t)(intptr_t)l;
    }
    default: {
        Map *m = pl_map_new(d[1] == 's');
        const char *vd = pl_skip_desc(d + 1);
        for (int k = 0; j && j->type == J_OBJ && k < j->len; k++) {
            int64_t key;
            if (d[1] == 's') key = (int64_t)(intptr_t)pl_strdup(j->keys[k]);
            else if (!pl_match_int(j->keys[k], &key)) continue;
            pl_map_set(m, key, pl_json_read(j->items[k], vd));
        }
        return (int64_t)(intptr_t)m;
    }
    }
}

char *pl_to_json(int64_t v, const char *desc) {
    Buf b = {0};
    pl_json_write(&b, v, desc);
    return buf_take(&b);
}

int64_t pl_from_json(const char *text, const char *desc) {
    J *root = jparse(nz(text));
    const char *rest = jparse_end();
    while (rest && (*rest == ' ' || *rest == '\n' || *rest == '\t' || *rest == '\r')) rest++;
    if (!root || (rest && *rest)) {
        jfree(root);
        pl_fail("The text is not valid JSON.");
    }
    int64_t v = pl_json_read(root, desc);
    jfree(root);
    return v;
}
