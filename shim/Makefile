CC = clang
CFLAGS = -Wall -Wextra -O2 -std=gnu99 -fPIC
LDFLAGS = -dynamiclib -L. -lmpv.2
TEST_LDFLAGS = -Wl,-rpath,/Applications/Stremio.app/Contents/MacOS

TARGET = libstremio-mpv-ipc-shim.dylib
TEST_TARGET = test_resolver

all: $(TARGET) $(TEST_TARGET)

$(TARGET): export_resolver.o mpv_ipc_shim.o libmpv.2.tbd
	$(CC) $(LDFLAGS) -o $@ export_resolver.o mpv_ipc_shim.o

test_resolver: test_resolver.o export_resolver.o
	$(CC) $(CFLAGS) $(TEST_LDFLAGS) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f *.o $(TARGET) $(TEST_TARGET)

.PHONY: all clean
