#ifndef PLAIN_JSON_H
#define PLAIN_JSON_H

#include "plain_rt.h"

enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };

typedef struct J {
    int type;
    long long i;
    double d;
    int is_float;
    char *s;
    struct J **items;
    char **keys;
    int len, cap;
} J;

J *jparse(const char *text);
J *jget(J *o, const char *key);
void jfree(J *j);
void json_text(Buf *b, const char *s);
const char *jparse_end(void);
void pl_json_write(Buf *b, int64_t v, const char *desc);
int64_t pl_json_read(J *j, const char *desc);

#endif
