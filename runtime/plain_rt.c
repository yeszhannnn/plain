/* Runtime linked into every program compiled by engc.
 *
 * Always present: output buffer, text helpers, random numbers.
 * With -DPLAIN_TELEGRAM (bots only, link with -lcurl): Telegram Bot API client,
 * a small JSON parser and per-user memory for "their ..." variables.
 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    char *p;
    size_t n, cap;
} Buf;

static void buf_put(Buf *b, const char *s, size_t n) {
    if (b->n + n + 1 > b->cap) {
        b->cap = (b->n + n + 1) * 2;
        b->p = realloc(b->p, b->cap);
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

static void buf_putc(Buf *b, char c) { buf_put(b, &c, 1); }
static void buf_puts(Buf *b, const char *s) { buf_put(b, s, strlen(s)); }

static char *buf_take(Buf *b) {
    char *p = b->p ? b->p : strdup("");
    b->p = NULL;
    b->n = b->cap = 0;
    return p;
}

/* ── output: parts are joined with one space, except before punctuation ── */

static Buf out;

static void out_piece(const char *s) {
    if (!s || !*s) return;
    if (out.n && !isspace((unsigned char)out.p[out.n - 1]) && !isspace((unsigned char)s[0]) &&
        !strchr(".,!?;:)", s[0]))
        buf_putc(&out, ' ');
    buf_puts(&out, s);
}

void pl_out_text(const char *s) { out_piece(s); }

void pl_out_int(long long v) {
    char tmp[32];
    snprintf(tmp, sizeof tmp, "%lld", v);
    out_piece(tmp);
}

void pl_out_print(void) {
    fwrite(out.p ? out.p : "", 1, out.n, stdout);
    fputc('\n', stdout);
    fflush(stdout);
    out.n = 0;
    if (out.p) out.p[0] = 0;
}

/* ── text ── */

static const char *nz(const char *s) { return s ? s : ""; }

int pl_text_cmp(const char *a, const char *b) { return strcmp(nz(a), nz(b)); }

char *pl_keep(const char *s) { return strdup(nz(s)); }

static void trim(const char **s, size_t *n) {
    while (*n && isspace((unsigned char)(*s)[0])) (*s)++, (*n)--;
    while (*n && isspace((unsigned char)(*s)[*n - 1])) (*n)--;
}

/* equal ignoring ASCII case and surrounding spaces: how "when someone sends" matches */
int pl_text_same(const char *a, const char *b) {
    a = nz(a), b = nz(b);
    size_t na = strlen(a), nb = strlen(b);
    trim(&a, &na);
    trim(&b, &nb);
    if (na != nb) return 0;
    for (size_t i = 0; i < na; i++)
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return 0;
    return 1;
}

int pl_starts(const char *h, const char *n) {
    h = nz(h), n = nz(n);
    for (; *n; h++, n++)
        if (!*h || tolower((unsigned char)*h) != tolower((unsigned char)*n)) return 0;
    return 1;
}

int pl_contains(const char *h, const char *n) {
    h = nz(h);
    do {
        if (pl_starts(h, n)) return 1;
    } while (*h++);
    return 0;
}

/* ── random ── */

static unsigned long long rng_state;

void pl_init(void) {
    rng_state = (unsigned long long)time(NULL) ^ ((unsigned long long)getpid() << 32) ^ 0x9E3779B97F4A7C15ULL;
}

long long pl_random(long long lo, long long hi) {
    if (hi < lo) {
        long long t = lo;
        lo = hi;
        hi = t;
    }
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    unsigned long long range = (unsigned long long)(hi - lo) + 1;
    return lo + (long long)(range ? rng_state % range : rng_state);
}

#ifdef PLAIN_TELEGRAM
#include <curl/curl.h>

/* ── JSON, just enough for the Bot API ── */

enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };

typedef struct J {
    int type;
    long long i;
    char *s;
    struct J **items;
    char **keys;
    int len, cap;
} J;

static const char *jp;

static void jfree(J *j) {
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

static void buf_utf8(Buf *b, unsigned cp) {
    if (cp < 0x80) buf_putc(b, cp);
    else if (cp < 0x800) buf_putc(b, 0xC0 | cp >> 6), buf_putc(b, 0x80 | (cp & 63));
    else if (cp < 0x10000)
        buf_putc(b, 0xE0 | cp >> 12), buf_putc(b, 0x80 | (cp >> 6 & 63)), buf_putc(b, 0x80 | (cp & 63));
    else
        buf_putc(b, 0xF0 | cp >> 18), buf_putc(b, 0x80 | (cp >> 12 & 63)), buf_putc(b, 0x80 | (cp >> 6 & 63)),
            buf_putc(b, 0x80 | (cp & 63));
}

static char *jstr(void) {
    Buf b = {0};
    jp++; /* opening quote */
    while (*jp && *jp != '"') {
        if (*jp != '\\' || !jp[1]) {
            buf_putc(&b, *jp++);
            continue;
        }
        jp++;
        char e = *jp++;
        switch (e) {
        case 'n': buf_putc(&b, '\n'); break;
        case 't': buf_putc(&b, '\t'); break;
        case 'r': buf_putc(&b, '\r'); break;
        case 'b': buf_putc(&b, '\b'); break;
        case 'f': buf_putc(&b, '\f'); break;
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
            buf_utf8(&b, cp);
            break;
        }
        default: buf_putc(&b, e);
        }
    }
    if (*jp == '"') jp++;
    return buf_take(&b);
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
        j->i = strtoll(jp, &end, 10);
        jp = end;
        while (*jp && strchr("0123456789.eE+-", *jp)) jp++;
        return j;
    }
    return NULL;
}

static J *jget(J *o, const char *key) {
    if (!o || o->type != J_OBJ) return NULL;
    for (int k = 0; k < o->len; k++)
        if (!strcmp(o->keys[k], key)) return o->items[k];
    return NULL;
}

static void json_text(Buf *b, const char *s) {
    buf_putc(b, '"');
    for (; *s; s++) {
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

/* ── HTTP ── */

static const char *api_base, *token;
static int idle_exit;

static size_t on_data(char *p, size_t size, size_t n, void *ud) {
    buf_put(ud, p, size * n);
    return size * n;
}

static J *api(const char *method, const char *body) {
    Buf url = {0}, resp = {0};
    buf_puts(&url, api_base);
    buf_puts(&url, "/bot");
    buf_puts(&url, token);
    buf_putc(&url, '/');
    buf_puts(&url, method);
    CURL *c = curl_easy_init();
    struct curl_slist *h = curl_slist_append(NULL, "Content-Type: application/json");
    curl_easy_setopt(c, CURLOPT_URL, url.p);
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, body);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_data);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &resp);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 90L);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    CURLcode rc = curl_easy_perform(c);
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    free(url.p);
    if (rc != CURLE_OK) {
        fprintf(stderr, "Network problem while talking to Telegram: %s\n", curl_easy_strerror(rc));
        free(resp.p);
        return NULL;
    }
    char *text = buf_take(&resp);
    jp = text;
    J *root = jval();
    free(text);
    J *ok = jget(root, "ok");
    if (!ok || !ok->i) {
        J *d = jget(root, "description");
        fprintf(stderr, "Telegram refused the request: %s\n", d && d->s ? d->s : "unknown error");
        exit(1);
    }
    return root;
}

/* ── the bot loop ── */

typedef struct {
    long long chat;
    long long *cells;
} User;

static User *users;
static int nusers, capusers, nslots;
static long long offset, cur_chat;
static char *cur_text, *cur_name;
static J *pending;
static int pending_i;
static char **buttons;
static int nbuttons;

void pl_bot_start(int slots) {
    nslots = slots;
    token = getenv("TELEGRAM_BOT_TOKEN");
    if (!token || !*token) {
        fprintf(stderr, "Set TELEGRAM_BOT_TOKEN to your bot's token first. You get one from @BotFather in Telegram.\n");
        exit(1);
    }
    api_base = getenv("TELEGRAM_API_URL");
    if (!api_base || !*api_base) api_base = "https://api.telegram.org";
    idle_exit = getenv("PLAIN_BOT_EXIT_WHEN_IDLE") != NULL;
    curl_global_init(CURL_GLOBAL_DEFAULT);
    fprintf(stderr, "The bot is running. Press Ctrl+C to stop.\n");
}

int pl_bot_next(void) {
    for (;;) {
        J *result = jget(pending, "result");
        while (result && pending_i < result->len) {
            J *u = result->items[pending_i++];
            J *id = jget(u, "update_id");
            if (id && id->i >= offset) offset = id->i + 1;
            J *m = jget(u, "message");
            J *t = jget(m, "text"), *chat = jget(m, "chat");
            if (!t || t->type != J_STR || !jget(chat, "id")) continue;
            J *fn = jget(jget(m, "from"), "first_name");
            free(cur_text);
            free(cur_name);
            cur_text = strdup(t->s);
            cur_name = strdup(fn && fn->s ? fn->s : "");
            cur_chat = jget(chat, "id")->i;
            fprintf(stderr, "[%s] %s\n", cur_name, cur_text);
            return 1;
        }
        jfree(pending);
        pending = NULL;
        pending_i = 0;
        char body[160];
        snprintf(body, sizeof body, "{\"offset\":%lld,\"timeout\":%d,\"allowed_updates\":[\"message\"]}", offset,
                 idle_exit ? 0 : 30);
        pending = api("getUpdates", body);
        if (!pending) {
            if (idle_exit) return 0;
            sleep(3);
            continue;
        }
        J *r = jget(pending, "result");
        if (idle_exit && (!r || r->len == 0)) return 0;
    }
}

const char *pl_msg_text(void) { return nz(cur_text); }
const char *pl_msg_name(void) { return nz(cur_name); }

int pl_msg_is_number(void) {
    const char *s = nz(cur_text);
    size_t n = strlen(s);
    trim(&s, &n);
    size_t i = (n && (s[0] == '-' || s[0] == '+')) ? 1 : 0;
    if (i == n || n - i > 18) return 0;
    for (; i < n; i++)
        if (!isdigit((unsigned char)s[i])) return 0;
    return 1;
}

long long pl_msg_number(void) { return pl_msg_is_number() ? strtoll(nz(cur_text), NULL, 10) : 0; }

void *pl_user_slot(int k) {
    for (int u = 0; u < nusers; u++)
        if (users[u].chat == cur_chat) return &users[u].cells[k];
    if (nusers == capusers) {
        capusers = capusers ? capusers * 2 : 16;
        users = realloc(users, capusers * sizeof *users);
    }
    users[nusers].chat = cur_chat;
    users[nusers].cells = calloc(nslots ? nslots : 1, sizeof(long long));
    return &users[nusers++].cells[k];
}

void pl_button(const char *s) {
    buttons = realloc(buttons, (nbuttons + 1) * sizeof *buttons);
    buttons[nbuttons++] = strdup(nz(s));
}

void pl_out_reply(void) {
    Buf b = {0};
    char head[64];
    snprintf(head, sizeof head, "{\"chat_id\":%lld,\"text\":", cur_chat);
    buf_puts(&b, head);
    json_text(&b, out.n ? out.p : " ");
    if (nbuttons) {
        buf_puts(&b, ",\"reply_markup\":{\"resize_keyboard\":true,\"keyboard\":[");
        for (int k = 0; k < nbuttons; k++) {
            if (k % 3 == 0) buf_puts(&b, k ? "],[" : "[");
            else buf_putc(&b, ',');
            buf_puts(&b, "{\"text\":");
            json_text(&b, buttons[k]);
            buf_putc(&b, '}');
            free(buttons[k]);
        }
        buf_puts(&b, "]]}");
        nbuttons = 0;
    }
    buf_putc(&b, '}');
    jfree(api("sendMessage", b.p));
    free(b.p);
    out.n = 0;
    if (out.p) out.p[0] = 0;
}
#endif
