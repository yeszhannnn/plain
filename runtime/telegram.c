/* Telegram Bot API over libcurl, per-user memory for "their ..." and saving memory to disk.
 * Built only for bots (programs with "when someone sends ..."). */
#include <curl/curl.h>
#include <gc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "json.h"

char *pl_out_take(void);

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
    if (rc != CURLE_OK) {
        fprintf(stderr, "Network problem while talking to Telegram: %s\n", curl_easy_strerror(rc));
        return NULL;
    }
    J *root = jparse(resp.p ? resp.p : "");
    J *ok = jget(root, "ok");
    if (!ok || !ok->i) {
        J *d = jget(root, "description");
        fflush(stdout);
        fprintf(stderr, "Telegram refused the request: %s\n", d && d->s ? d->s : "unknown error");
        exit(1);
    }
    return root;
}

/* ── memory: per-user variables and saving to disk ── */

typedef struct {
    long long chat;
    int64_t *cells;
} User;

typedef struct {
    const char *name, *desc;
    void *addr;
} Global;

static User *users;
static int nusers, capusers, nslots;
static const char **slot_names, **slot_descs;
static long long cur_chat;
static const char *persist_path;
static const Global *globals;
static int nglobals;

static User *user(long long chat) {
    for (int u = 0; u < nusers; u++)
        if (users[u].chat == chat) return &users[u];
    if (nusers == capusers) {
        capusers = capusers ? capusers * 2 : 16;
        User *grown = GC_MALLOC(capusers * sizeof *grown);
        if (users) memcpy(grown, users, nusers * sizeof *users);
        users = grown;
    }
    User *u = &users[nusers++];
    u->chat = chat;
    u->cells = GC_MALLOC((nslots ? nslots : 1) * sizeof(int64_t));
    for (int k = 0; k < nslots; k++) u->cells[k] = pl_zero_slot(slot_descs[k]);
    return u;
}

void *pl_user_slot(int k) { return &user(cur_chat)->cells[k]; }

static int64_t global_get(const Global *g) {
    if (*g->desc == 'b') return *(unsigned char *)g->addr & 1;
    return *(int64_t *)g->addr;
}

static void global_set(const Global *g, int64_t v) {
    if (*g->desc == 'b') *(unsigned char *)g->addr = v != 0;
    else *(int64_t *)g->addr = v;
}

static void save(void) {
    Buf b = {0};
    buf_puts(&b, "{\"globals\": {");
    for (int k = 0; k < nglobals; k++) {
        if (k) buf_puts(&b, ", ");
        json_text(&b, globals[k].name);
        buf_puts(&b, ": ");
        pl_json_write(&b, global_get(&globals[k]), globals[k].desc);
    }
    buf_puts(&b, "}, \"users\": {");
    for (int u = 0; u < nusers; u++) {
        char tmp[32];
        snprintf(tmp, sizeof tmp, "%s\"%lld\": {", u ? ", " : "", users[u].chat);
        buf_puts(&b, tmp);
        for (int k = 0; k < nslots; k++) {
            if (k) buf_puts(&b, ", ");
            json_text(&b, slot_names[k]);
            buf_puts(&b, ": ");
            pl_json_write(&b, users[u].cells[k], slot_descs[k]);
        }
        buf_putc(&b, '}');
    }
    buf_puts(&b, "}}");
    Buf tmp = {0};
    buf_puts(&tmp, persist_path);
    buf_puts(&tmp, ".tmp");
    FILE *f = fopen(tmp.p, "wb");
    if (!f) {
        fprintf(stderr, "I can't save the memory to \"%s\".\n", persist_path);
        return;
    }
    fwrite(b.p, 1, b.n, f);
    fclose(f);
    rename(tmp.p, persist_path);
}

void pl_persist(const char *path, const Global *table, int n) {
    persist_path = pl_strdup(path);
    globals = table;
    nglobals = n;
    FILE *f = fopen(persist_path, "rb");
    if (!f) return;
    Buf b = {0};
    char chunk[65536];
    size_t got;
    while ((got = fread(chunk, 1, sizeof chunk, f)) > 0) buf_put(&b, chunk, got);
    fclose(f);
    J *root = jparse(b.p ? b.p : "");
    J *gs = jget(root, "globals"), *us = jget(root, "users");
    for (int k = 0; k < n; k++) {
        J *v = jget(gs, table[k].name);
        if (v) global_set(&table[k], pl_json_read(v, table[k].desc));
    }
    for (int u = 0; us && us->type == J_OBJ && u < us->len; u++) {
        int64_t chat;
        if (!pl_match_int(us->keys[u], &chat)) continue;
        User *usr = user(chat);
        for (int k = 0; k < nslots; k++) {
            J *v = jget(us->items[u], slot_names[k]);
            if (v) usr->cells[k] = pl_json_read(v, slot_descs[k]);
        }
    }
    jfree(root);
}

/* ── the bot loop ── */

static long long offset;
static char *cur_text, *cur_name;
static J *pending;
static int pending_i, handled;
static char **buttons;
static int nbuttons;

void pl_bot_start(int n, const char **names, const char **descs) {
    nslots = n;
    slot_names = names;
    slot_descs = descs;
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
    if (handled && persist_path) save();
    handled = 0;
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
            cur_text = pl_strdup(t->s);
            cur_name = pl_strdup(fn && fn->s ? fn->s : "");
            cur_chat = jget(chat, "id")->i;
            fprintf(stderr, "[%s] %s\n", cur_name, cur_text);
            handled = 1;
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
int pl_msg_is_number(void) { return pl_match_int(cur_text, NULL); }

long long pl_msg_number(void) {
    int64_t v;
    return pl_match_int(cur_text, &v) ? v : 0;
}

void pl_button(const char *s) {
    char **grown = GC_MALLOC((nbuttons + 1) * sizeof *grown);
    if (buttons) memcpy(grown, buttons, nbuttons * sizeof *buttons);
    buttons = grown;
    buttons[nbuttons++] = pl_strdup(s);
}

void pl_out_reply(void) {
    char *text = pl_out_take();
    Buf b = {0};
    char head[64];
    snprintf(head, sizeof head, "{\"chat_id\":%lld,\"text\":", cur_chat);
    buf_puts(&b, head);
    json_text(&b, *text ? text : " ");
    if (nbuttons) {
        buf_puts(&b, ",\"reply_markup\":{\"resize_keyboard\":true,\"keyboard\":[");
        for (int k = 0; k < nbuttons; k++) {
            if (k % 3 == 0) buf_puts(&b, k ? "],[" : "[");
            else buf_putc(&b, ',');
            buf_puts(&b, "{\"text\":");
            json_text(&b, buttons[k]);
            buf_putc(&b, '}');
        }
        buf_puts(&b, "]]}");
        nbuttons = 0;
        buttons = NULL;
    }
    buf_putc(&b, '}');
    jfree(api("sendMessage", b.p));
}
