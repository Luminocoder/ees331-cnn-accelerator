/* Minimal compiler-required ARM EABI zeroing; outside the timed kernels. */
typedef __SIZE_TYPE__ size_t;
void *memset(void *p,int v,size_t n) { volatile unsigned char *s=p; while (n--) *s++=(unsigned char)v; return p; }
void __aeabi_memclr(void *p,size_t n) { volatile unsigned char *s=p; while (n--) *s++=0; }
void __aeabi_memclr4(void *p,size_t n) { __aeabi_memclr(p,n); }
void __aeabi_memclr8(void *p,size_t n) { __aeabi_memclr(p,n); }
