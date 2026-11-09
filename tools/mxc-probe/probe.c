/* Fixed, synthetic Windows x64 probe. No C runtime or shell. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#define PATH_CAP 1024

typedef struct {
    const char *label;
    const WCHAR *request;
    const WCHAR *canonical;
    BOOL absolute;
} Target;

static const Target targets[] = {
    {"allowed", L"allowed.txt", L"work\\allowed.txt", FALSE},
    {"inside_junction", L"inside-link\\linked.txt", L"work\\inside\\linked.txt", FALSE},
    {"outside", L"..\\protected\\outside.txt", L"protected\\outside.txt", FALSE},
    {"outside_absolute", L"protected\\outside.txt", L"protected\\outside.txt", TRUE},
    {"fake_ledger", L"..\\protected\\fake-ledger.txt", L"protected\\fake-ledger.txt", FALSE},
    {"fake_credential", L"..\\protected\\fake-credential.txt", L"protected\\fake-credential.txt", FALSE},
    {"outside_junction", L"outside-link\\linked.txt", L"protected\\linked.txt", FALSE},
};

static WCHAR module[PATH_CAP], cwd[PATH_CAP], request[PATH_CAP];
static WCHAR expected[PATH_CAP], resolved[PATH_CAP];
static HANDLE output;
static DWORD root_length;

static BOOL same(const WCHAR *left, const WCHAR *right)
{
    while (*left && *left == *right) {
        ++left;
        ++right;
    }
    return *left == *right;
}

static BOOL emit(const char *text)
{
    DWORD written, length = 0;
    while (text[length]) ++length;
    return WriteFile(output, text, length, &written, NULL) && written == length;
}

static BOOL emit_error(DWORD code)
{
    static char decimal[11];
    unsigned pos = 10;
    do {
        decimal[--pos] = (char)('0' + code % 10);
        code /= 10;
    } while (code);
    return emit(decimal + pos);
}

static const WCHAR *mode(void)
{
    const WCHAR *arg = GetCommandLineW();
    if (*arg == L'"') {
        ++arg;
        while (*arg && *arg != L'"') ++arg;
        if (*arg != L'"') return L"";
        ++arg;
    } else {
        while (*arg && *arg != L' ' && *arg != L'\t') ++arg;
    }
    if (*arg != L' ' && *arg != L'\t') return L"";
    while (*arg == L' ' || *arg == L'\t') ++arg;
    return arg;
}

static BOOL locate_fixture(const WCHAR *prefix)
{
    DWORD length = GetModuleFileNameW(NULL, module, PATH_CAP);
    DWORD prefix_length = (DWORD)lstrlenW(prefix);
    DWORD work_end, leaf, i;
    if (length >= PATH_CAP || length < 4 || module[1] != L':' || module[2] != L'\\')
        return FALSE;
    work_end = length;
    while (work_end && module[work_end - 1] != L'\\') --work_end;
    if (!work_end || !same(module + work_end, L"mxc-probe.exe"))
        return FALSE;
    --work_end;
    if (work_end >= PATH_CAP) return FALSE;
    *(module + work_end) = 0;
    length = GetCurrentDirectoryW(PATH_CAP, cwd);
    if (!length || length >= PATH_CAP || !same(module, cwd))
        return FALSE;
    root_length = work_end;
    while (root_length && module[root_length - 1] != L'\\') --root_length;
    if (!root_length || !same(module + root_length, L"work"))
        return FALSE;
    leaf = root_length - 1;
    while (leaf && module[leaf - 1] != L'\\') --leaf;
    if (root_length - 1 - leaf != prefix_length + 32)
        return FALSE;
    for (i = 0; i < prefix_length; ++i)
        if (module[leaf + i] != prefix[i]) return FALSE;
    for (i = leaf + prefix_length; i < root_length - 1; ++i) {
        WCHAR ch = module[i];
        if (!((ch >= L'0' && ch <= L'9') || (ch >= L'a' && ch <= L'f')))
            return FALSE;
    }
    return TRUE;
}

static BOOL make_path(WCHAR *buffer, DWORD offset, const WCHAR *suffix)
{
    /* Keep this bounded copy independent of compiler-emitted CRT memcpy. */
    volatile WCHAR *destination = buffer + offset;
    DWORD i = 0;
    if (offset + root_length + lstrlenW(suffix) >= PATH_CAP) return FALSE;
    for (; i < root_length; ++i) destination[i] = module[i];
    do { destination[i++] = *suffix; } while (*suffix++);
    return TRUE;
}

/* FALSE means the fixture or the probe failed, not that Windows denied I/O. */
static BOOL attempt(const Target *target, BOOL write, DWORD *code)
{
    /* One invocation at a time. Avoid an address-taken stack aggregate at
       this CRT-free entry point, which does not initialize a GS cookie. */
    static BY_HANDLE_FILE_INFORMATION info;
    DWORD count, length;
    char byte = 'X';
    BOOL ok = FALSE;
    HANDLE file;
    if ((target->absolute && !make_path(request, 0, target->request)) ||
        !make_path(expected, 4, target->canonical)) return FALSE;
    file = CreateFileW(target->absolute ? request : target->request, write ? GENERIC_WRITE : GENERIC_READ,
                       FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        *code = GetLastError();
        return TRUE;
    }
    length = GetFinalPathNameByHandleW(file, resolved, PATH_CAP, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    if (length && length < PATH_CAP && same(resolved, expected) &&
        GetFileInformationByHandle(file, &info) && info.nNumberOfLinks == 1 &&
        !(info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) &&
        !info.nFileSizeHigh && info.nFileSizeLow > 0 && info.nFileSizeLow <= 64) {
        BOOL transferred = write ? WriteFile(file, &byte, 1, &count, NULL)
                                 : ReadFile(file, &byte, 1, &count, NULL);
        *code = transferred ? ERROR_SUCCESS : GetLastError();
        ok = !transferred || count == 1;
    }
    return CloseHandle(file) && ok;
}

static BOOL emit_boundary_result(const Target *target, BOOL write)
{
    DWORD code;
    if (!attempt(target, write, &code) || !emit(target->label) || !emit("="))
        return FALSE;
    if (code == ERROR_SUCCESS) return emit("OK\n");
    if (code == ERROR_ACCESS_DENIED) return emit("DENIED_5\n");
    return emit("ERROR_") && emit_error(code) && emit("\n");
}

void probe_entry(void)
{
    static const Target allowed_read = {"ALLOWED_READ", L"allowed.txt", L"work\\allowed.txt", FALSE};
    static const Target allowed_write = {"ALLOWED_WRITE", L"allowed.txt", L"work\\allowed.txt", FALSE};
    static const Target protected_read = {
        "PROTECTED_READ", L"..\\protected\\protected.txt", L"protected\\protected.txt", FALSE
    };
    const WCHAR *arg = mode();
    BOOL boundary = same(arg, L"--app-server-file-test");
    unsigned i;
    output = GetStdHandle(STD_OUTPUT_HANDLE);
    if (same(arg, L"--self-test"))
        ExitProcess(emit("MXC_NATIVE_OK\n") ? 0 : 2);
    if ((!boundary && !same(arg, L"--file-test")) ||
        !locate_fixture(boundary ? L"app-server-" : L"mxc-native-"))
        ExitProcess(2);
    expected[0] = L'\\';
    expected[1] = L'\\';
    expected[2] = L'?';
    expected[3] = L'\\';
    if (boundary)
        ExitProcess(emit_boundary_result(&allowed_read, FALSE) &&
                    emit_boundary_result(&allowed_write, TRUE) &&
                    emit_boundary_result(&protected_read, FALSE) ? 0 : 2);
    for (i = 0; i < ARRAYSIZE(targets); ++i) {
        DWORD read, write;
        if (!attempt(&targets[i], FALSE, &read) || !attempt(&targets[i], TRUE, &write) ||
            !emit(targets[i].label) || !emit("\t") || !emit_error(read) ||
            !emit("\t") || !emit_error(write) || !emit("\n")) ExitProcess(2);
    }
    ExitProcess(0);
}
