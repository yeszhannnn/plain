/* Runtime for programs compiled by engc. Memory comes from the Boehm garbage collector.
 *
 * Every value inside a list or a table is a 64-bit slot: whole numbers as is,
 * decimals as their bits, yes/no as 0 or 1, texts, lists and tables as pointers.
 * A short type code ("desc") tells the runtime how to read a slot:
 *   i whole number, f decimal, s text, b yes/no, L<item> list, M<key><value> table.
 */
#ifndef PLAIN_RT_H
#define PLAIN_RT_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    char *p;
    size_t n, cap;
} Buf;

typedef struct {
    int64_t len, cap;
    int64_t *items;
    int plain;      /* items are numbers or yes/no: the GC does not need to look inside */
} List;

typedef struct Map Map;

extern int pl_line;
extern const char *pl_file;

void *pl_alloc(size_t n);
char *pl_alloc_text(size_t n);
char *pl_strdup(const char *s);
const char *nz(const char *s);
void pl_fail(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));

void buf_put(Buf *b, const char *s, size_t n);
void buf_putc(Buf *b, char c);
void buf_puts(Buf *b, const char *s);
char *buf_take(Buf *b);
void buf_piece(Buf *b, const char *s);
const char *pl_format(Buf *b, int64_t v, const char *desc);
const char *pl_skip_desc(const char *desc);
void pl_format_float(char *out, size_t size, double x);

int pl_slot_equal(int64_t a, int64_t b, char kind);
int pl_slot_compare(int64_t a, int64_t b, char kind);
int64_t pl_zero_slot(const char *desc);

int pl_match_int(const char *s, int64_t *out);
int pl_match_decimal(const char *s, double *out);

List *pl_list_new(void);
List *pl_list_of(const char *item_desc);
int64_t pl_list_len(List *l);
int64_t pl_list_get(List *l, int64_t i);
void pl_list_add(List *l, int64_t v);

Map *pl_map_new(int text_keys);
int64_t pl_map_len(Map *m);
List *pl_map_keys(Map *m);
int32_t pl_map_has(Map *m, int64_t key);
int64_t pl_map_get_or_new(Map *m, int64_t key, const char *desc);
void pl_map_set(Map *m, int64_t key, int64_t value);
int pl_map_text_keys(Map *m);
int64_t pl_map_value_at(Map *m, int64_t key);

char *pl_lower(const char *s);

/* kinds of things declared in the program ("a book has a title and a year") */
typedef struct {
    const char *name;
    int32_t nfields;
    const char **fields;      /* field names as written */
    const char **descs;       /* field type codes */
} Kind;

const Kind *pl_kind(const char *desc);   /* desc starts with 'K' */

#endif
