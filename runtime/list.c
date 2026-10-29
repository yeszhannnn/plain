/* Lists: 1-based positions, as in everyday speech. */
#include <stdlib.h>
#include <string.h>

#include "plain_rt.h"

List *pl_list_new(void) {
    List *l = pl_alloc(sizeof *l);
    l->len = l->cap = 0;
    l->items = NULL;
    l->plain = 0;
    return l;
}

/* a new list; lists of numbers or yes/no values live in memory the GC does not scan */
List *pl_list_of(const char *item_desc) {
    List *l = pl_list_new();
    l->plain = *item_desc == 'i' || *item_desc == 'f' || *item_desc == 'b';
    return l;
}

int64_t pl_list_len(List *l) { return l ? l->len : 0; }

void pl_list_add(List *l, int64_t v) {
    if (l->len == l->cap) {
        int64_t cap = l->cap ? l->cap * 2 : 8;
        int64_t *items = l->plain ? (int64_t *)pl_alloc_text(cap * sizeof *items) : pl_alloc(cap * sizeof *items);
        if (l->items) memcpy(items, l->items, l->len * sizeof *items);
        l->items = items;
        l->cap = cap;
    }
    l->items[l->len++] = v;
}

static int64_t position(List *l, int64_t i) {
    if (i == -1) {
        if (!l->len) pl_fail("There is no last item: the list is empty.");
        return l->len - 1;
    }
    if (i < 1 || i > l->len)
        pl_fail("There is no item %lld: the list has %lld item%s.", (long long)i, (long long)l->len,
                l->len == 1 ? "" : "s");
    return i - 1;
}

int64_t pl_list_get(List *l, int64_t i) { return l->items[position(l, i)]; }
void pl_list_set(List *l, int64_t i, int64_t v) { l->items[position(l, i)] = v; }

void pl_list_remove_at(List *l, int64_t i) {
    int64_t p = position(l, i);
    memmove(l->items + p, l->items + p + 1, (l->len - p - 1) * sizeof *l->items);
    l->len--;
}

void pl_list_remove_value(List *l, int64_t v, const char *desc) {
    for (int64_t i = 0; i < l->len; i++)
        if (pl_slot_equal(l->items[i], v, *desc)) {
            pl_list_remove_at(l, i + 1);
            return;
        }
}

int32_t pl_list_contains(List *l, int64_t v, const char *desc) {
    for (int64_t i = 0; i < l->len; i++)
        if (pl_slot_equal(l->items[i], v, *desc)) return 1;
    return 0;
}

/* insertion sort keeps equal items in order, like Python's sort; merge for long lists */
static void merge_sort(int64_t *a, int64_t *tmp, int64_t n, char kind) {
    if (n < 2) return;
    if (n <= 16) {
        for (int64_t i = 1; i < n; i++) {
            int64_t v = a[i], j = i;
            while (j > 0 && pl_slot_compare(a[j - 1], v, kind) > 0) a[j] = a[j - 1], j--;
            a[j] = v;
        }
        return;
    }
    int64_t h = n / 2;
    merge_sort(a, tmp, h, kind);
    merge_sort(a + h, tmp, n - h, kind);
    int64_t i = 0, j = h, k = 0;
    while (i < h && j < n) tmp[k++] = pl_slot_compare(a[j], a[i], kind) < 0 ? a[j++] : a[i++];
    while (i < h) tmp[k++] = a[i++];
    while (j < n) tmp[k++] = a[j++];
    memcpy(a, tmp, n * sizeof *a);
}

/* stable sort of positions by their keys; descending keeps equal keys in their old order, like Python */
static const int64_t *sort_keys;
static char sort_kind;
static int sort_desc;

static int key_compare(int64_t a, int64_t b) {
    int c = pl_slot_compare(sort_keys[a], sort_keys[b], sort_kind);
    return sort_desc ? -c : c;
}

static void merge_positions(int64_t *a, int64_t *tmp, int64_t n) {
    if (n < 2) return;
    int64_t h = n / 2;
    merge_positions(a, tmp, h);
    merge_positions(a + h, tmp, n - h);
    int64_t i = 0, j = h, k = 0;
    while (i < h && j < n) tmp[k++] = key_compare(a[j], a[i]) < 0 ? a[j++] : a[i++];
    while (i < h) tmp[k++] = a[i++];
    while (j < n) tmp[k++] = a[j++];
    memcpy(a, tmp, n * sizeof *a);
}

void pl_list_sort_by(List *l, List *keys, const char *key_desc, int32_t descending) {
    int64_t n = l->len;
    if (n < 2) return;
    int64_t *pos = (int64_t *)pl_alloc_text(n * sizeof *pos), *tmp = (int64_t *)pl_alloc_text(n * sizeof *tmp);
    for (int64_t i = 0; i < n; i++) pos[i] = i;
    sort_keys = keys->items, sort_kind = *key_desc, sort_desc = descending;
    merge_positions(pos, tmp, n);
    int64_t *items = l->plain ? (int64_t *)pl_alloc_text(n * sizeof *items) : pl_alloc(n * sizeof *items);
    for (int64_t i = 0; i < n; i++) items[i] = l->items[pos[i]];
    memcpy(l->items, items, n * sizeof *items);
}

void pl_list_sort(List *l, const char *desc, int32_t descending) {
    if (l->len < 2) return;
    if (descending) {
        pl_list_sort_by(l, l, desc, 1);
        return;
    }
    int64_t *tmp = l->plain ? (int64_t *)pl_alloc_text(l->len * sizeof *tmp) : pl_alloc(l->len * sizeof *tmp);
    merge_sort(l->items, tmp, l->len, *desc);
}

int64_t pl_list_best(List *l, List *keys, const char *key_desc, int32_t largest) {
    if (!l->len) pl_fail("There is no %s item in an empty list.", largest ? "largest" : "smallest");
    int64_t best = 0;
    for (int64_t i = 1; i < l->len; i++) {
        int c = pl_slot_compare(keys->items[i], keys->items[best], *key_desc);
        if (largest ? c > 0 : c < 0) best = i;
    }
    return l->items[best];
}

void pl_list_reverse(List *l) {
    for (int64_t i = 0, j = l->len - 1; i < j; i++, j--) {
        int64_t t = l->items[i];
        l->items[i] = l->items[j];
        l->items[j] = t;
    }
}

List *pl_list_slice(List *l, int64_t a, int64_t b) {
    List *out = pl_list_new();
    out->plain = l->plain;
    if (a < 1) a = 1;
    if (b > l->len) b = l->len;
    for (int64_t i = a; i <= b; i++) pl_list_add(out, l->items[i - 1]);
    return out;
}

int64_t pl_random(int64_t lo, int64_t hi);

int64_t pl_list_random(List *l) {
    if (!l->len) pl_fail("I can't pick from an empty list.");
    return l->items[pl_random(0, l->len - 1)];
}

int64_t pl_list_sum_int(List *l) {
    uint64_t s = 0;
    for (int64_t i = 0; i < l->len; i++) s += (uint64_t)l->items[i];
    return (int64_t)s;
}

static double as_double(int64_t v, char kind) {
    if (kind == 'f') {
        double x;
        memcpy(&x, &v, sizeof x);
        return x;
    }
    return (double)v;
}

double pl_list_sum_float(List *l) {
    double s = 0;
    for (int64_t i = 0; i < l->len; i++) s += as_double(l->items[i], 'f');
    return s;
}

double pl_list_average(List *l, const char *desc) {
    if (!l->len) pl_fail("There is no average of an empty list.");
    double s = 0;
    for (int64_t i = 0; i < l->len; i++) s += as_double(l->items[i], *desc);
    return s / l->len;
}

int64_t pl_list_extreme(List *l, const char *desc, int32_t largest) {
    if (!l->len) pl_fail("There is no %s item in an empty list.", largest ? "largest" : "smallest");
    int64_t best = l->items[0];
    for (int64_t i = 1; i < l->len; i++) {
        int c = pl_slot_compare(l->items[i], best, *desc);
        if (largest ? c > 0 : c < 0) best = l->items[i];
    }
    return best;
}
