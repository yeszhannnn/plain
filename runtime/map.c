/* Tables (dictionaries): keys are texts or whole numbers; entries keep the order they were added in. */
#include <string.h>

#include "plain_rt.h"

struct Map {
    int text_keys;
    int64_t count, used, cap;   /* live entries, entries ever used, entry capacity */
    int64_t *keys, *vals;
    char *alive;
    int64_t *index;             /* open addressing: -1 empty, -2 removed, else entry number */
    int64_t index_cap;
};

Map *pl_map_new(int text_keys) {
    Map *m = pl_alloc(sizeof *m);
    memset(m, 0, sizeof *m);
    m->text_keys = text_keys;
    return m;
}

int pl_map_text_keys(Map *m) { return m->text_keys; }
int64_t pl_map_len(Map *m) { return m ? m->count : 0; }

static uint64_t hash(Map *m, int64_t key) {
    uint64_t h;
    if (m->text_keys) {
        h = 1469598103934665603ULL;
        for (const unsigned char *s = (const unsigned char *)nz((const char *)(intptr_t)key); *s; s++)
            h = (h ^ *s) * 1099511628211ULL;
    } else {
        h = (uint64_t)key + 0x9E3779B97F4A7C15ULL;
        h = (h ^ (h >> 30)) * 0xBF58476D1CE4E5B9ULL;
        h = (h ^ (h >> 27)) * 0x94D049BB133111EBULL;
        h ^= h >> 31;
    }
    return h;
}

static int same(Map *m, int64_t a, int64_t b) { return pl_slot_equal(a, b, m->text_keys ? 's' : 'i'); }

static int64_t find(Map *m, int64_t key) {
    if (!m->index_cap) return -1;
    uint64_t mask = m->index_cap - 1;
    for (uint64_t i = hash(m, key) & mask;; i = (i + 1) & mask) {
        int64_t e = m->index[i];
        if (e == -1) return -1;
        if (e >= 0 && same(m, m->keys[e], key)) return e;
    }
}

static void reindex(Map *m, int64_t cap) {
    /* drop removed entries, keeping order */
    int64_t k = 0;
    for (int64_t e = 0; e < m->used; e++)
        if (m->alive[e]) {
            m->keys[k] = m->keys[e];
            m->vals[k] = m->vals[e];
            m->alive[k] = 1;
            k++;
        }
    m->used = k;
    m->index_cap = cap;
    m->index = (int64_t *)pl_alloc_text(cap * sizeof *m->index);
    memset(m->index, 0xFF, cap * sizeof *m->index);
    uint64_t mask = cap - 1;
    for (int64_t e = 0; e < m->used; e++) {
        uint64_t i = hash(m, m->keys[e]) & mask;
        while (m->index[i] != -1) i = (i + 1) & mask;
        m->index[i] = e;
    }
}

void pl_map_set(Map *m, int64_t key, int64_t value) {
    int64_t e = find(m, key);
    if (e >= 0) {
        m->vals[e] = value;
        return;
    }
    if (m->used == m->cap) {
        int64_t cap = m->cap ? m->cap * 2 : 8;
        int64_t *keys = pl_alloc(cap * sizeof *keys), *vals = pl_alloc(cap * sizeof *vals);
        char *alive = pl_alloc_text(cap);
        if (m->used) {
            memcpy(keys, m->keys, m->used * sizeof *keys);
            memcpy(vals, m->vals, m->used * sizeof *vals);
            memcpy(alive, m->alive, m->used);
        }
        m->keys = keys, m->vals = vals, m->alive = alive, m->cap = cap;
    }
    if ((m->used + 1) * 2 > m->index_cap) reindex(m, m->index_cap ? m->index_cap * 2 : 16);
    e = m->used++;
    m->keys[e] = key;
    m->vals[e] = value;
    m->alive[e] = 1;
    m->count++;
    uint64_t mask = m->index_cap - 1, i = hash(m, key) & mask;
    while (m->index[i] >= 0) i = (i + 1) & mask;
    m->index[i] = e;
}

int32_t pl_map_has(Map *m, int64_t key) { return find(m, key) >= 0; }

int64_t pl_map_value_at(Map *m, int64_t key) { return m->vals[find(m, key)]; }

int64_t pl_map_get(Map *m, int64_t key, const char *what) {
    int64_t e = find(m, key);
    if (e < 0) {
        if (m->text_keys) pl_fail("There is no %s of \"%s\".", what, nz((const char *)(intptr_t)key));
        pl_fail("There is no %s of %lld.", what, (long long)key);
    }
    return m->vals[e];
}

int64_t pl_map_get_or_new(Map *m, int64_t key, const char *desc) {
    int64_t e = find(m, key);
    if (e >= 0) return m->vals[e];
    int64_t v = pl_zero_slot(desc);
    pl_map_set(m, key, v);
    return v;
}

void pl_map_remove(Map *m, int64_t key) {
    if (!m->index_cap) return;
    uint64_t mask = m->index_cap - 1;
    for (uint64_t i = hash(m, key) & mask;; i = (i + 1) & mask) {
        int64_t e = m->index[i];
        if (e == -1) return;
        if (e >= 0 && same(m, m->keys[e], key)) {
            m->index[i] = -2;
            m->alive[e] = 0;
            m->count--;
            if (m->used > 16 && m->count * 2 < m->used) reindex(m, m->index_cap);
            return;
        }
    }
}

List *pl_map_keys(Map *m) {
    List *l = pl_list_new();
    for (int64_t e = 0; m && e < m->used; e++)
        if (m->alive[e]) pl_list_add(l, m->keys[e]);
    return l;
}
