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
        else if (c < 0x20) {
            char tmp[8];
            snprintf(tmp, sizeof tmp, "\\u%04x", c);
            buf_puts(b, tmp);
        } else buf_putc(b, c);
    }
    buf_putc(b, '"');
}
