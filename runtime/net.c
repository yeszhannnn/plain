/* HTTP requests over libcurl: "fetch ... into page" and "send ... to ...". */
#include <curl/curl.h>
#include <string.h>

#include "plain_rt.h"

static size_t on_data(char *p, size_t size, size_t n, void *ud) {
    buf_put(ud, p, size * n);
    return size * n;
}

static char *request(const char *url, const char *body, const char *type) {
    static int ready;
    if (!ready) {
        curl_global_init(CURL_GLOBAL_DEFAULT);
        ready = 1;
    }
    url = nz(url);
    Buf resp = {0}, header = {0};
    CURL *c = curl_easy_init();
    struct curl_slist *h = NULL;
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, on_data);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &resp);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 60L);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    if (body) {
        buf_puts(&header, "Content-Type: ");
        buf_puts(&header, type);
        h = curl_slist_append(NULL, header.p);
        curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
        curl_easy_setopt(c, CURLOPT_POSTFIELDS, body);
        curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)strlen(body));
    }
    CURLcode rc = curl_easy_perform(c);
    long status = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &status);
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    if (rc != CURLE_OK) pl_fail("I couldn't reach %s.", url);
    if (status >= 400) pl_fail("The request to %s failed with status %ld.", url, status);
    return buf_take(&resp);
}

char *pl_fetch(const char *url) { return request(url, NULL, NULL); }

/* json: 1 always JSON, 2 JSON if the text looks like it */
char *pl_post(const char *body, const char *url, int32_t json) {
    body = nz(body);
    const char *p = body;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    int is_json = json == 1 || (json == 2 && (*p == '{' || *p == '['));
    return request(url, body, is_json ? "application/json" : "text/plain; charset=utf-8");
}
