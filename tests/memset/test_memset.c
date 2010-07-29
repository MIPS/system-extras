#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <cutils/memory.h>

static inline uint32_t
get_count(void)
{
  uint32_t res;
  asm volatile ("rdhwr %[res],$2" : [res] "=r" (res));
  return res;
}

uint32_t
timeone(void (*fn)(), void *d, uint32_t val, uint32_t bytes)
{
  uint32_t start, stop, delta;
  start = get_count();
  (*fn)(d, val, bytes);
  stop = get_count();
  delta = stop - start;
  return delta * 2;
}

/* define VERIFY to check that memset only touches the bytes it's supposed to */
/*#define VERIFY*/

/*
 * Using a big arena means that memset will most likely miss in the cache
 * NB Enabling verification effectively warms up the cache...
 */
#define ARENASIZE 0x1000000
#ifdef VERIFY
char arena[ARENASIZE+8];	/* Allow space for guard words */
#else
char arena[ARENASIZE];
#endif

void
testone(char *tag, void (*fn)(), int trials, int minbytes, int maxbytes, int size, int threshold)
{
  int offset;
  void *d;
  void *p;
  uint32_t v, notv = 0;
  uint32_t n;
  int i, cycles;
  int totalcycles = 0, totalbytes = 0, samples = 0;

  /* Reset RNG to ensure each test uses same random values */
  srand(0);			/* FIXME should be able to use some other seed than 0 */

  for (i = 0; i < trials; i++) {
    n = minbytes + (rand() % (maxbytes-minbytes));	/* How many bytes to do */
    offset = ((rand() % (ARENASIZE-n)));		/* Where to start */

#ifdef VERIFY
    offset += 4;		/* Allow space for guard word at beginning */
#endif
    v = rand();

    /* Adjust alignment and sizes based on transfer size */
    switch (size) {
    case 1:
      v &= 0xff;
      notv = ~v & 0xff;
      break;
    case 2:
      v &= 0xffff;
      notv = ~v & 0xffff;
      offset &= ~1;
      n &= ~1;
      break;
    case 4:
      notv = ~v;
      offset &= ~3;
      n &= ~3;
      break;
    }

    d = &arena[offset];

#ifdef VERIFY
    /* Initialise the area and guard words */ 
    for (p = &arena[offset-4]; p < (void *)&arena[offset+n+4]; p = (void *)((uint32_t)p + size)) {
      if (size == 1)
	*(uint8_t *)p = notv;
      else if (size == 2)
	*(uint16_t *)p = notv;
      else if (size == 4)
	*(uint32_t *)p = notv;
    }
#endif
    cycles = timeone(fn, d, v, n);
#ifdef VERIFY
    /* Check the area and guard words */
    for (p = &arena[offset-4]; p < (void *)&arena[offset+n+4]; p = (void *)((uint32_t)p + size)) {
      uint32_t got = 0;
      if (size == 1)
	got = *(uint8_t *)p;
      else if (size == 2)
	got = *(uint16_t *)p;
      else if (size == 4)
	got = *(uint32_t *)p;
      if (p < (void *)&arena[offset]) {
	if (got != notv)
	  printf ("%s: verify failure: preguard:%p d=%p v=%08x got=%08x n=%d\n", tag, p, d, v, got, n);
      }
      else if (p < (void *)&arena[offset+n]) {
	if (got != v)
	  printf ("%s: verify failure: arena:%p d=%p v=%08x got=%08x n=%d\n", tag, p, d, v, n);
      }
      else {
	if (got != notv)
	  printf ("%s: verify failure: postguard:%p d=%p v=%08x got=%08x n=%d\n", tag, p, d, v, n);
      }
    }
#endif

    /* If the cycle count looks reasonable include it in the statistics */
    if (cycles < threshold) {
      totalbytes += n;
      totalcycles += cycles;
      samples++;
    }
  }

  printf("%s: samples=%d avglen=%d avgcycles=%d bpc=%g\n",
	 tag, samples, totalbytes/samples, totalcycles/samples, (double)totalbytes/(double)totalcycles);
}

extern void old_android_memset32(uint32_t* dst, uint32_t value, size_t size);
extern void old_android_memset16(uint32_t* dst, uint16_t value, size_t size);

int
main(int argc, char **argv)
{
  struct {
    char *type;
    int trials;
    int minbytes, maxbytes;
  } *pp, params[] = {
    {"small",  10000,   0,   64},
    {"medium", 10000,  64,  512},
    {"large",  10000, 512, 1280},
    {"varied", 10000,   0, 1280},
  };
#define NPARAMS (sizeof(params)/sizeof(params[0]))
  struct {
    char *name;
    void (*fn)();
    int size;
  } *fp, functions[] = {
    {"omemset16", (void (*)())old_android_memset16, 2},
    {"omemset32", (void (*)())old_android_memset32, 4},
    {"memset16",  (void (*)())android_memset16,     2},
    {"memset32",  (void (*)())android_memset32,     4},
    {"memset",    (void (*)())memset,               1},
  };
#define NFUNCTIONS (sizeof(functions)/sizeof(functions[0]))
  char tag[40];
  int threshold;

  /* Warm up the page cache */
  memset(arena, 0xff, ARENASIZE); /* use 0xff now to avoid COW later */

  for (fp = functions; fp < &functions[NFUNCTIONS]; fp++) {
    for (pp = params; pp < &params[NPARAMS]; pp++) {
      sprintf(tag, "%10s: %7s %4d-%4d", fp->name, pp->type, pp->minbytes, pp->maxbytes);

      /* Set the cycle threshold */
      threshold = pp->maxbytes * 4;
      testone(tag, fp->fn, pp->trials, pp->minbytes, pp->maxbytes, fp->size, threshold);
    }
    printf ("\n");
  }

  return 0;
}
