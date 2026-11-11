// One-shot P2.116 trial. The only client destinations this tool constructs are loopback.
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <sddl.h>
#include <aclapi.h>
#include <lm.h>
#include <bcrypt.h>
#include <initguid.h>
#include <fwpmu.h>
#include <cstdio>
#include <fcntl.h>
#include <io.h>
#include <string>
#include <vector>

#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "fwpuclnt.lib")
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "netapi32.lib")
#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "user32.lib")

static constexpr wchar_t Account[] = L"DevOrchP2116";
static constexpr wchar_t Tag[] = L"DevOrchestra P2.116 loopback-only pilot 54b49f93";
static GUID Provider = {0x54b49f93,0xeb64,0x4c60,{0x87,0x4b,0x87,0xdc,0x27,0x4c,0xc6,0x01}};
static GUID Sublayer = {0x54b49f93,0xeb64,0x4c60,{0x87,0x4b,0x87,0xdc,0x27,0x4c,0xc6,0x02}};

struct Failure { const char* operation; DWORD error; };
static void check(DWORD code, const char* operation) { if (code) throw Failure{operation, code}; }
static void require(BOOL ok, const char* operation) { if (!ok) throw Failure{operation, GetLastError()}; }
static void report(const char* stage, DWORD value) { std::printf("%s=%lu\n", stage, value); std::fflush(stdout); }

struct Handle {
    HANDLE value = nullptr;
    ~Handle() { if (value) CloseHandle(value); }
    Handle() = default;
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};
struct PilotMutex {
    Handle handle;
    bool owned = false;
    ~PilotMutex() { if (owned) ReleaseMutex(handle.value); }
};
struct Engine {
    HANDLE value = nullptr;
    Engine() { check(FwpmEngineOpen0(nullptr, RPC_C_AUTHN_WINNT, nullptr, nullptr, &value), "WFP_OPEN"); }
    ~Engine() { FwpmEngineClose0(value); }
};
struct Transaction {
    HANDLE engine;
    bool active = true;
    explicit Transaction(HANDLE handle) : engine(handle) { check(FwpmTransactionBegin0(engine, 0), "WFP_BEGIN"); }
    ~Transaction() { if (active) FwpmTransactionAbort0(engine); }
    void commit() { check(FwpmTransactionCommit0(engine), "WFP_COMMIT"); active = false; }
};
struct Descriptor {
    PSECURITY_DESCRIPTOR data = nullptr;
    explicit Descriptor(const std::wstring& sddl) {
        require(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &data, nullptr), "SDDL");
    }
    ~Descriptor() { LocalFree(data); }
};
struct Socket {
    SOCKET value = INVALID_SOCKET;
    explicit Socket(SOCKET sock) : value(sock) {
        if (value == INVALID_SOCKET) throw Failure{"SOCKET", static_cast<DWORD>(WSAGetLastError())};
    }
    ~Socket() { closesocket(value); }
    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;
};
struct Listener {
    Socket sock;
    sockaddr_storage address{};
    int length;
    unsigned short port;
    explicit Listener(int family) : sock(socket(family, SOCK_STREAM, IPPROTO_TCP)),
        length(family == AF_INET ? sizeof(sockaddr_in) : sizeof(sockaddr_in6)) {
        const int one = 1;
        if (setsockopt(sock.value, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&one), sizeof(one)))
            throw Failure{"EXCLUSIVE_LISTENER", static_cast<DWORD>(WSAGetLastError())};
        if (family == AF_INET) {
            auto& a = reinterpret_cast<sockaddr_in&>(address);
            a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        } else {
            auto& a = reinterpret_cast<sockaddr_in6&>(address);
            a.sin6_family = AF_INET6; a.sin6_addr = in6addr_loopback;
            if (setsockopt(sock.value, IPPROTO_IPV6, IPV6_V6ONLY, reinterpret_cast<const char*>(&one), sizeof(one)))
                throw Failure{"IPV6_ONLY", static_cast<DWORD>(WSAGetLastError())};
        }
        if (bind(sock.value, reinterpret_cast<sockaddr*>(&address), length) || listen(sock.value, 1) ||
            getsockname(sock.value, reinterpret_cast<sockaddr*>(&address), &length))
            throw Failure{"LOOPBACK_LISTEN", static_cast<DWORD>(WSAGetLastError())};
        port = ntohs(family == AF_INET ? reinterpret_cast<sockaddr_in&>(address).sin_port :
                     reinterpret_cast<sockaddr_in6&>(address).sin6_port);
    }
    bool accepted() const {
        fd_set readable;
        FD_ZERO(&readable); FD_SET(sock.value, &readable);
        timeval timeout{0, 400000};
        const int result = select(0, &readable, nullptr, nullptr, &timeout);
        if (result < 0) throw Failure{"LISTENER_SELECT", static_cast<DWORD>(WSAGetLastError())};
        if (!result) return false;
        const Socket peer(accept(sock.value, nullptr, nullptr));
        return true;
    }
    void positive() const {
        const Socket control(socket(address.ss_family, SOCK_STREAM, IPPROTO_TCP));
        if (connect(control.value, reinterpret_cast<const sockaddr*>(&address), length) || !accepted())
            throw Failure{"HOST_POSITIVE_CONTROL", static_cast<DWORD>(WSAGetLastError())};
    }
};

static std::wstring account_sid() {
    wchar_t machine[MAX_COMPUTERNAME_LENGTH + 1]{};
    DWORD machine_size = ARRAYSIZE(machine);
    require(GetComputerNameW(machine, &machine_size), "COMPUTER_NAME");
    auto qualified = std::wstring(machine) + L"\\" + Account;
    BYTE sid[SECURITY_MAX_SID_SIZE]{};
    wchar_t domain[256]{};
    DWORD sid_size = sizeof(sid), domain_size = ARRAYSIZE(domain);
    SID_NAME_USE type{};
    require(LookupAccountNameW(nullptr, qualified.c_str(), sid, &sid_size, domain, &domain_size, &type), "ACCOUNT_SID");
    if (type != SidTypeUser) throw Failure{"ACCOUNT_TYPE", ERROR_INVALID_DATA};
    wchar_t* value = nullptr;
    require(ConvertSidToStringSidW(sid, &value), "SID_STRING");
    const std::wstring result(value);
    LocalFree(value);
    return result;
}

static GUID filter_key(unsigned index) {
    auto key = Provider;
    key.Data4[7] = static_cast<unsigned char>(16 + index);
    return key;
}

static void absent(HANDLE engine) {
    FWPM_PROVIDER0* provider = nullptr;
    FWPM_SUBLAYER0* sublayer = nullptr;
    const auto p = FwpmProviderGetByKey0(engine, &Provider, &provider);
    const auto s = FwpmSubLayerGetByKey0(engine, &Sublayer, &sublayer);
    FwpmFreeMemory0(reinterpret_cast<void**>(&provider));
    FwpmFreeMemory0(reinterpret_cast<void**>(&sublayer));
    if (p != FWP_E_PROVIDER_NOT_FOUND || s != FWP_E_SUBLAYER_NOT_FOUND)
        throw Failure{"WFP_NAMESPACE_OCCUPIED", ERROR_ALREADY_EXISTS};
    for (unsigned index = 0; index < 16; ++index) {
        auto key = filter_key(index);
        FWPM_FILTER0* filter = nullptr;
        auto status = FwpmFilterGetByKey0(engine, &key, &filter);
        FwpmFreeMemory0(reinterpret_cast<void**>(&filter));
        if (status != FWP_E_FILTER_NOT_FOUND) throw Failure{"WFP_FILTER_OCCUPIED", ERROR_ALREADY_EXISTS};
    }
}

static void install(HANDLE engine, const std::wstring& sid, const std::wstring& executable,
                    unsigned short ipv4, unsigned short ipv6, const std::wstring& tag) {
    Descriptor match(L"D:P(A;;0x00000001;;;" + sid + L")");
    FWP_BYTE_BLOB user{GetSecurityDescriptorLength(match.data), reinterpret_cast<UINT8*>(match.data)};
    FWP_BYTE_BLOB* app = nullptr;
    check(FwpmGetAppIdFromFileName0(executable.c_str(), &app), "WFP_APP_ID");
    try {
        Transaction transaction(engine);
        absent(engine);
        FWPM_PROVIDER0 provider{};
        provider.providerKey = Provider;
        provider.displayData.name = const_cast<wchar_t*>(tag.c_str());
        provider.flags = FWPM_PROVIDER_FLAG_PERSISTENT;
        check(FwpmProviderAdd0(engine, &provider, nullptr), "WFP_PROVIDER_ADD");
        FWPM_SUBLAYER0 sub{};
        sub.subLayerKey = Sublayer; sub.providerKey = &Provider;
        sub.displayData.name = const_cast<wchar_t*>(tag.c_str());
        sub.flags = FWPM_SUBLAYER_FLAG_PERSISTENT;
        sub.weight = 0x100;
        check(FwpmSubLayerAdd0(engine, &sub, nullptr), "WFP_SUBLAYER_ADD");
        FWP_BYTE_ARRAY16 address6{};
        address6.byteArray16[15] = 1;
        unsigned index = 0;
        for (bool v6 : {false, true}) {
            FWPM_FILTER_CONDITION0 condition[2]{};
            condition[0].fieldKey = FWPM_CONDITION_ALE_USER_ID;
            condition[0].matchType = FWP_MATCH_EQUAL;
            condition[0].conditionValue.type = FWP_SECURITY_DESCRIPTOR_TYPE;
            condition[0].conditionValue.sd = &user;
            FWPM_FILTER_CONDITION0 rejected[4]{};
            rejected[0].fieldKey = FWPM_CONDITION_ALE_APP_ID;
            rejected[0].conditionValue.type = FWP_BYTE_BLOB_TYPE;
            rejected[0].conditionValue.byteBlob = app;
            rejected[1].fieldKey = FWPM_CONDITION_IP_PROTOCOL;
            rejected[1].conditionValue.type = FWP_UINT8;
            rejected[1].conditionValue.uint8 = IPPROTO_TCP;
            rejected[2].fieldKey = FWPM_CONDITION_IP_REMOTE_ADDRESS;
            if (v6) {
                rejected[2].conditionValue.type = FWP_BYTE_ARRAY16_TYPE;
                rejected[2].conditionValue.byteArray16 = &address6;
            } else {
                rejected[2].conditionValue.type = FWP_UINT32;
                rejected[2].conditionValue.uint32 = INADDR_LOOPBACK;
            }
            rejected[3].fieldKey = FWPM_CONDITION_IP_REMOTE_PORT;
            rejected[3].conditionValue.type = FWP_UINT16;
            rejected[3].conditionValue.uint16 = v6 ? ipv6 : ipv4;
            // The complement of the approved tuple. None of these blocks overlaps it.
            for (const auto& field : rejected) for (auto comparison : {FWP_MATCH_LESS, FWP_MATCH_GREATER}) {
                condition[1] = field;
                condition[1].matchType = comparison;
                FWPM_FILTER0 filter{};
                filter.filterKey = filter_key(index++);
                filter.displayData.name = const_cast<wchar_t*>(tag.c_str());
                filter.providerKey = &Provider;
                filter.subLayerKey = Sublayer;
                filter.layerKey = v6 ? FWPM_LAYER_ALE_AUTH_CONNECT_V6 : FWPM_LAYER_ALE_AUTH_CONNECT_V4;
                filter.flags = FWPM_FILTER_FLAG_PERSISTENT;
                filter.weight.type = FWP_EMPTY;
                filter.action.type = FWP_ACTION_BLOCK;
                filter.numFilterConditions = ARRAYSIZE(condition);
                filter.filterCondition = condition;
                check(FwpmFilterAdd0(engine, &filter, nullptr, nullptr), "WFP_FILTER_ADD");
            }
        }
        transaction.commit();
    } catch (...) {
        FwpmFreeMemory0(reinterpret_cast<void**>(&app));
        throw;
    }
    FwpmFreeMemory0(reinterpret_cast<void**>(&app));
}

static void verify_filters(HANDLE engine, const std::wstring& tag) {
    for (unsigned index = 0; index < 16; ++index) {
        auto key = filter_key(index);
        FWPM_FILTER0* filter = nullptr;
        check(FwpmFilterGetByKey0(engine, &key, &filter), "WFP_FILTER_READ");
        const bool valid = filter->providerKey && IsEqualGUID(*filter->providerKey, Provider) &&
            IsEqualGUID(filter->subLayerKey, Sublayer) && (filter->flags & FWPM_FILTER_FLAG_PERSISTENT) &&
            filter->action.type == FWP_ACTION_BLOCK && filter->numFilterConditions == 2 &&
            filter->displayData.name && tag == filter->displayData.name;
        FwpmFreeMemory0(reinterpret_cast<void**>(&filter));
        if (!valid) throw Failure{"WFP_OWNERSHIP", ERROR_INVALID_DATA};
    }
}

static void remove_filters(HANDLE engine, const std::wstring& tag) {
    Transaction transaction(engine);
    verify_filters(engine, tag);
    FWPM_PROVIDER0* provider = nullptr;
    check(FwpmProviderGetByKey0(engine, &Provider, &provider), "WFP_PROVIDER_READ");
    const bool same_provider = provider->displayData.name && tag == provider->displayData.name;
    FwpmFreeMemory0(reinterpret_cast<void**>(&provider));
    FWPM_SUBLAYER0* sublayer = nullptr;
    check(FwpmSubLayerGetByKey0(engine, &Sublayer, &sublayer), "WFP_SUBLAYER_READ");
    const bool same_sublayer = sublayer->displayData.name && tag == sublayer->displayData.name &&
        sublayer->providerKey && IsEqualGUID(*sublayer->providerKey, Provider);
    FwpmFreeMemory0(reinterpret_cast<void**>(&sublayer));
    if (!same_provider || !same_sublayer) throw Failure{"WFP_OWNERSHIP", ERROR_INVALID_DATA};
    for (unsigned index = 0; index < 16; ++index) {
        auto key = filter_key(index);
        check(FwpmFilterDeleteByKey0(engine, &key), "WFP_FILTER_DELETE");
    }
    check(FwpmSubLayerDeleteByKey0(engine, &Sublayer), "WFP_SUBLAYER_DELETE");
    check(FwpmProviderDeleteByKey0(engine, &Provider), "WFP_PROVIDER_DELETE");
    transaction.commit();
    absent(engine);
}

static std::wstring token_sid(HANDLE token) {
    alignas(TOKEN_USER) BYTE buffer[sizeof(TOKEN_USER) + SECURITY_MAX_SID_SIZE]{};
    DWORD returned = 0;
    require(GetTokenInformation(token, TokenUser, buffer, sizeof(buffer), &returned), "TOKEN_USER");
    wchar_t* sid = nullptr;
    require(ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(buffer)->User.Sid, &sid), "TOKEN_USER_SID");
    const std::wstring result(sid);
    LocalFree(sid);
    return result;
}

static DWORD token_elevation(HANDLE process, const std::wstring* expected_sid = nullptr) {
    Handle token;
    require(OpenProcessToken(process, TOKEN_QUERY, &token.value), "TOKEN_OPEN");
    TOKEN_ELEVATION state{};
    DWORD returned = 0;
    require(GetTokenInformation(token.value, TokenElevation, &state, sizeof(state), &returned), "TOKEN_ELEVATION");
    if (expected_sid && token_sid(token.value) != *expected_sid)
        throw Failure{"WRONG_PROBE_IDENTITY", ERROR_ACCESS_DENIED};
    return state.TokenIsElevated;
}

static JOBOBJECT_BASIC_ACCOUNTING_INFORMATION job_counts(HANDLE job) {
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION counts{};
    if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &counts, sizeof(counts), nullptr))
        throw Failure{"PROCESS_DRAIN_UNCONFIRMED", GetLastError()};
    return counts;
}

static bool wait_for_empty_job(HANDLE job) {
    const auto deadline = GetTickCount64() + 3000;
    while (job_counts(job).ActiveProcesses) {
        if (GetTickCount64() >= deadline) return false;
        Sleep(10);
    }
    return true;
}

struct JobAttributes {
    std::vector<BYTE> storage;
    LPPROC_THREAD_ATTRIBUTE_LIST list = nullptr;
    HANDLE job;
    explicit JobAttributes(HANDLE owned_job) : job(owned_job) {
        SIZE_T size = 0;
        if (InitializeProcThreadAttributeList(nullptr, 1, 0, &size) ||
            GetLastError() != ERROR_INSUFFICIENT_BUFFER || !size)
            throw Failure{"JOB_ATTRIBUTE_SIZE", ERROR_INVALID_DATA};
        storage.resize(size);
        auto attributes = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
        require(InitializeProcThreadAttributeList(attributes, 1, 0, &size), "JOB_ATTRIBUTE_INIT");
        if (!UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST, &job, sizeof(job),
                                       nullptr, nullptr)) {
            const DWORD error = GetLastError();
            DeleteProcThreadAttributeList(attributes);
            throw Failure{"JOB_ATTRIBUTE_UPDATE", error};
        }
        list = attributes;
    }
    JobAttributes(const JobAttributes&) = delete;
    JobAttributes& operator=(const JobAttributes&) = delete;
    ~JobAttributes() { DeleteProcThreadAttributeList(list); }
};

static DWORD run_in_new_job(const std::wstring& executable, std::wstring& command, const wchar_t* cwd,
                            wchar_t* environment, const wchar_t* desktop, HANDLE token,
                            const std::wstring* expected_sid, bool preflight = false) {
    if (!token && !preflight) throw Failure{"PRIMARY_TOKEN_REQUIRED", ERROR_INVALID_HANDLE};
    Handle job, process, thread;
    job.value = CreateJobObjectW(nullptr, nullptr);
    require(job.value != nullptr, "JOB_CREATE");
    DWORD handle_flags = 0;
    require(GetHandleInformation(job.value, &handle_flags), "JOB_HANDLE_FLAGS");
    if (handle_flags & HANDLE_FLAG_INHERIT) throw Failure{"JOB_HANDLE_INHERITABLE", ERROR_INVALID_DATA};
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    limits.BasicLimitInformation.ActiveProcessLimit = 1;
    require(SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits)), "JOB_LIMIT");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION applied{};
    require(QueryInformationJobObject(job.value, JobObjectExtendedLimitInformation, &applied, sizeof(applied),
                                      nullptr), "JOB_LIMIT_READBACK");
    if (applied.BasicLimitInformation.LimitFlags != limits.BasicLimitInformation.LimitFlags ||
        applied.BasicLimitInformation.ActiveProcessLimit != limits.BasicLimitInformation.ActiveProcessLimit)
        throw Failure{"JOB_LIMIT_UNVERIFIED", ERROR_INVALID_DATA};
    JobAttributes attributes(job.value);
    STARTUPINFOEXW start{};
    start.StartupInfo.cb = sizeof(start);
    start.StartupInfo.lpDesktop = const_cast<wchar_t*>(desktop);
    start.lpAttributeList = attributes.list;
    PROCESS_INFORMATION pi{};
    constexpr DWORD flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT |
                            EXTENDED_STARTUPINFO_PRESENT;
    const BOOL created = token
        ? CreateProcessAsUserW(token, executable.c_str(), command.data(), nullptr, nullptr, FALSE,
                               flags, environment, cwd, &start.StartupInfo, &pi)
        : CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, FALSE,
                         flags, environment, cwd, &start.StartupInfo, &pi);
    const DWORD create_error = created ? ERROR_SUCCESS : GetLastError();
    process.value = pi.hProcess;
    thread.value = pi.hThread;
    if (!created) {
        if (process.value || thread.value || job_counts(job.value).TotalProcesses)
            throw Failure{"PROCESS_DRAIN_UNCONFIRMED", create_error};
        throw Failure{token ? "ATOMIC_TOKEN_CREATE" : "ATOMIC_CONTROL_CREATE", create_error};
    }
    BOOL assigned = FALSE;
    const bool membership = IsProcessInJob(process.value, job.value, &assigned) && assigned;
    if (!membership) {
        // Never resume an unverified process, even when the creation API reports success.
        if ((!TerminateProcess(process.value, 5) && WaitForSingleObject(process.value, 0) != WAIT_OBJECT_0) ||
            WaitForSingleObject(process.value, 3000) != WAIT_OBJECT_0 || !wait_for_empty_job(job.value))
            throw Failure{"PROCESS_DRAIN_UNCONFIRMED", ERROR_BUSY};
        throw Failure{"ATOMIC_MEMBERSHIP_UNVERIFIED", ERROR_INVALID_DATA};
    }
    try {
        if (job_counts(job.value).ActiveProcesses != 1)
            throw Failure{"PROCESS_DRAIN_UNCONFIRMED", ERROR_INVALID_DATA};
        if (token_elevation(process.value, expected_sid))
            throw Failure{"PROBE_MUST_BE_STANDARD_USER", ERROR_ACCESS_DENIED};
        if (preflight) report("JOB_MEMBERSHIP_BEFORE_RESUME", 1);
        if (ResumeThread(thread.value) != 1 || WaitForSingleObject(process.value, 8000) != WAIT_OBJECT_0)
            throw Failure{"PROBE_TIMEOUT_OR_RESUME", WAIT_TIMEOUT};
        if (!wait_for_empty_job(job.value))
            throw Failure{"PROCESS_DRAIN_UNCONFIRMED", ERROR_BUSY};
    } catch (...) {
        if (WaitForSingleObject(process.value, 0) != WAIT_OBJECT_0 || job_counts(job.value).ActiveProcesses) {
            if (!TerminateJobObject(job.value, 5) || WaitForSingleObject(process.value, 3000) != WAIT_OBJECT_0 ||
                !wait_for_empty_job(job.value))
                throw Failure{"PROCESS_DRAIN_UNCONFIRMED", ERROR_BUSY};
        }
        throw;
    }
    DWORD exit_code = 0;
    require(GetExitCodeProcess(process.value, &exit_code), "CLIENT_EXIT_CODE");
    return exit_code;
}

static void open_receipt(const wchar_t* name = L"pilot-result.txt") {
    wchar_t path[32768]{};
    const auto size = GetModuleFileNameW(nullptr, path, ARRAYSIZE(path));
    if (!size || size >= ARRAYSIZE(path)) throw Failure{"RECEIPT_PATH", ERROR_PATH_NOT_FOUND};
    const auto directory = std::wstring(path).substr(0, std::wstring(path).find_last_of(L'\\'));
    const auto filename = directory + L"\\" + name;
    PSID owner = nullptr;
    PSECURITY_DESCRIPTOR original = nullptr;
    check(GetNamedSecurityInfoW(const_cast<wchar_t*>(directory.c_str()), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                &owner, nullptr, nullptr, nullptr, &original), "RECEIPT_DIRECTORY_OWNER");
    wchar_t* sid = nullptr;
    const BOOL converted = ConvertSidToStringSidW(owner, &sid);
    const auto error = GetLastError();
    LocalFree(original);
    if (!converted) throw Failure{"RECEIPT_OWNER_SID", error};
    const std::wstring rule = L"D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;" + std::wstring(sid) + L")";
    LocalFree(sid);
    Descriptor acl(rule);
    SECURITY_ATTRIBUTES access{sizeof(access), acl.data, FALSE};
    const auto file = CreateFileW(filename.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &access, CREATE_NEW,
                                  FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (file == INVALID_HANDLE_VALUE) throw Failure{"NEW_RECEIPT_REQUIRED", GetLastError()};
    const int fd = _open_osfhandle(reinterpret_cast<intptr_t>(file), _O_WRONLY | _O_TEXT);
    if (fd < 0) { CloseHandle(file); throw Failure{"RECEIPT_HANDLE", ERROR_INVALID_HANDLE}; }
    // A GUI or hidden launch can have no inherited stdout. Reopen the CRT stream on NUL first.
    FILE* stream = nullptr;
    if (freopen_s(&stream, "NUL", "w", stdout) != 0 || _dup2(fd, _fileno(stdout)) != 0) {
        _close(fd); throw Failure{"RECEIPT_STREAM", ERROR_INVALID_HANDLE};
    }
    _close(fd);
}

struct PrivateDesktop {
    HWINSTA station = nullptr;
    HDESK desktop = nullptr;
    std::wstring full_name;
    void create(const std::wstring& sid) {
        // The existing interactive window station and desktop ACLs stay untouched.
        const auto name = L"DevOrchP2116-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64());
        Descriptor acl(L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;" + sid + L")");
        SECURITY_ATTRIBUTES access{sizeof(access), acl.data, FALSE};
        const HWINSTA original = GetProcessWindowStation();
        require(original != nullptr, "OWNER_WINDOW_STATION");
        SetLastError(ERROR_SUCCESS);
        station = CreateWindowStationW(name.c_str(), 0, WINSTA_ALL_ACCESS, &access);
        require(station != nullptr, "PRIVATE_WINDOW_STATION");
        if (GetLastError() == ERROR_ALREADY_EXISTS) throw Failure{"PRIVATE_STATION_COLLISION", ERROR_ALREADY_EXISTS};
        require(SetProcessWindowStation(station), "ENTER_PRIVATE_STATION");
        desktop = CreateDesktopW(L"Probe", nullptr, nullptr, 0, GENERIC_ALL, &access);
        const auto error = GetLastError();
        require(SetProcessWindowStation(original), "RESTORE_OWNER_STATION");
        if (!desktop) throw Failure{"PRIVATE_DESKTOP_CREATE", error};
        full_name = name + L"\\Probe";
    }
    ~PrivateDesktop() {
        if (desktop) CloseDesktop(desktop);
        if (station) CloseWindowStation(station);
    }
};

static int job_preflight() {
    try {
        if (token_elevation(GetCurrentProcess())) throw Failure{"PREFLIGHT_REQUIRES_STANDARD_TOKEN", ERROR_ACCESS_DENIED};
        wchar_t system[MAX_PATH]{}, windows[MAX_PATH]{};
        if (!GetSystemDirectoryW(system, ARRAYSIZE(system)) || !GetWindowsDirectoryW(windows, ARRAYSIZE(windows)))
            throw Failure{"DIRECTORY_DISCOVERY", ERROR_PATH_NOT_FOUND};
        Handle current, restricted;
        require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
                                 &current.value), "TOKEN_OPEN");
        const auto sid = token_sid(current.value);
        const std::wstring executable = std::wstring(system) + L"\\cmd.exe";
        const auto base_command = L"\"" + executable + L"\" /d /q /c exit 0";
        std::wstring environment = L"SystemRoot=" + std::wstring(windows);
        environment.push_back(0);
        auto command = base_command;
        const auto run = [&](HANDLE token) {
            return run_in_new_job(executable, command, system, environment.data(), nullptr, token, &sid, true);
        };
        const DWORD control = run(nullptr);
        report("ATOMIC_CONTROL_EXIT", control);
        if (control) return 2;
        command = base_command;
        try {
            run(INVALID_HANDLE_VALUE);
            throw Failure{"INVALID_TOKEN_WAS_ACCEPTED", ERROR_INVALID_DATA};
        } catch (Failure error) {
            if (strcmp(error.operation, "ATOMIC_TOKEN_CREATE") || error.error != ERROR_INVALID_HANDLE) throw;
            report("INVALID_TOKEN_CREATE_BLOCKED", error.error);
        }
        require(CreateRestrictedToken(current.value, DISABLE_MAX_PRIVILEGE, 0, nullptr, 0, nullptr, 0, nullptr,
                                      &restricted.value), "RESTRICTED_TOKEN_CREATE");
        command = base_command;
        const DWORD result = run(restricted.value);
        report("ATOMIC_RESTRICTED_EXIT", result);
        return result ? 2 : 0;
    } catch (Failure error) {
        report(error.operation, error.error);
        return 2;
    }
}

static DWORD run_client(const std::wstring& ps, wchar_t* password, const std::wstring& cwd,
                        const std::wstring& home, const std::wstring& sid, const std::wstring& desktop,
                        const Listener& listener) {
    // No profile, DNS, browser, model, or external endpoints. No user supplied command.
    const bool v6 = listener.address.ss_family == AF_INET6;
    std::wstring script = L"$ErrorActionPreference='Stop';try{[IO.File]::WriteAllText($env:TEMP+'\\probe-stage.txt','START');"
                          L"$a=[Net.IPAddress]::Parse('";
    script += v6 ? L"::1" : L"127.0.0.1";
    script += L"');$c=[Net.Sockets.TcpClient]::new($a.AddressFamily);try{"
              L"if(!$c.ConnectAsync($a," + std::to_wstring(listener.port) +
              L").Wait(2500)){exit 2};exit 0}finally{$c.Dispose()}}catch{"
              L"if($_.Exception.GetBaseException().NativeErrorCode -eq 10013){exit 13};exit 3}";
    auto command = L"\"" + ps + L"\" -NoLogo -NoProfile -NonInteractive -Command \"" + script + L"\"";
    if (command.size() >= 1024) throw Failure{"PROBE_COMMAND_TOO_LONG", ERROR_INVALID_PARAMETER};
    wchar_t windows[MAX_PATH]{};
    if (!GetWindowsDirectoryW(windows, ARRAYSIZE(windows))) throw Failure{"WINDOWS_ENVIRONMENT", ERROR_PATH_NOT_FOUND};
    std::wstring environment;
    for (const auto& item : {L"APPDATA=" + home, L"LOCALAPPDATA=" + home,
                             L"PATH=" + std::wstring(windows) + L"\\System32", L"SystemRoot=" + std::wstring(windows),
                             L"TEMP=" + home, L"TMP=" + home, L"USERPROFILE=" + home, L"WINDIR=" + std::wstring(windows)}) {
        environment += item;
        environment.push_back(0);
    }
    Handle token;
    require(LogonUserW(Account, L".", password, LOGON32_LOGON_INTERACTIVE, LOGON32_PROVIDER_DEFAULT,
                       &token.value), "STANDARD_USER_TOKEN");
    return run_in_new_job(ps, command, cwd.c_str(), environment.data(), desktop.c_str(), token.value, &sid);
}

static bool own_account(const std::wstring& tag, bool must_be_disabled = false) {
    LPBYTE info = nullptr;
    const auto status = NetUserGetInfo(nullptr, Account, 1, &info);
    if (status == NERR_UserNotFound) return false;
    check(status, "ACCOUNT_READ");
    auto account = reinterpret_cast<USER_INFO_1*>(info);
    const bool owned = account->usri1_comment && tag == account->usri1_comment &&
        (!must_be_disabled || (account->usri1_flags & UF_ACCOUNTDISABLE));
    NetApiBufferFree(info);
    if (!owned) throw Failure{"ACCOUNT_NAME_IN_USE", ERROR_ALREADY_EXISTS};
    return true;
}

static bool directory_present(const std::wstring& path) {
    const auto attributes = GetFileAttributesW(path.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        const auto error = GetLastError();
        if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) return false;
        throw Failure{"PILOT_PATH_QUERY", error};
    }
    if (!(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        throw Failure{"UNSAFE_PILOT_DIRECTORY", ERROR_INVALID_DATA};
    return true;
}

static void trial_marker(const std::wstring& path, const std::wstring& id, bool create) {
    Handle file;
    file.value = CreateFileW(path.c_str(), create ? GENERIC_WRITE : GENERIC_READ, 0, nullptr,
                             create ? CREATE_NEW : OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (file.value == INVALID_HANDLE_VALUE) {
        file.value = nullptr;
        throw Failure{"TRIAL_MARKER_OPEN", GetLastError()};
    }
    BY_HANDLE_FILE_INFORMATION info{};
    require(GetFileInformationByHandle(file.value, &info), "TRIAL_MARKER_IDENTITY");
    if (info.nNumberOfLinks != 1 || info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))
        throw Failure{"UNSAFE_TRIAL_MARKER", ERROR_INVALID_DATA};
    DWORD transferred = 0;
    const DWORD bytes = static_cast<DWORD>(id.size() * sizeof(wchar_t));
    if (create) {
        require(WriteFile(file.value, id.data(), bytes, &transferred, nullptr) && transferred == bytes &&
                FlushFileBuffers(file.value), "TRIAL_MARKER_WRITE");
    } else {
        wchar_t observed[33]{};
        require(ReadFile(file.value, observed, sizeof(observed), &transferred, nullptr), "TRIAL_MARKER_READ");
        if (transferred != bytes || id != std::wstring(observed, 32))
            throw Failure{"TRIAL_MARKER_MISMATCH", ERROR_INVALID_DATA};
    }
}

static void recover_disabled_trial(HANDLE engine, const std::wstring& parent, const std::wstring& root,
                                   const std::wstring& id, const std::wstring& tag) {
    const bool exists = directory_present(parent);
    const auto marker = parent + L"\\P2116-disabled-trial.id";
    if (exists) trial_marker(marker, id, false);
    const bool account = own_account(tag, true);
    FWPM_PROVIDER0* provider = nullptr;
    const auto status = FwpmProviderGetByKey0(engine, &Provider, &provider);
    FwpmFreeMemory0(reinterpret_cast<void**>(&provider));
    if (status == ERROR_SUCCESS) {
        if (!exists || !account) throw Failure{"TRIAL_OWNERSHIP_INCOMPLETE", ERROR_INVALID_DATA};
        verify_filters(engine, tag);
        report("POLICY_PRESENT_AFTER_FAULT", 16);
        remove_filters(engine, tag);
    } else {
        absent(engine);
    }
    if (account) {
        if (!exists) throw Failure{"TRIAL_MARKER_REQUIRED", ERROR_INVALID_DATA};
        check(NetUserDel(nullptr, Account), "REMOVE_DISABLED_OWNED_ACCOUNT");
    }
    if (exists) {
        if (directory_present(root)) require(RemoveDirectoryW(root.c_str()), "REMOVE_EMPTY_PILOT_DIRECTORY");
        require(DeleteFileW(marker.c_str()), "REMOVE_OWNED_TRIAL_MARKER");
        require(RemoveDirectoryW(parent.c_str()), "REMOVE_EMPTY_PILOT_PARENT");
    }
    { Engine independent; absent(independent.value); }
    if (own_account(tag, true) || directory_present(parent)) throw Failure{"RECOVERY_READBACK", ERROR_BUSY};
    report("DISABLED_TRIAL_ABSENCE_VERIFIED", 1);
}

static bool valid_id(const std::wstring& id) {
    return id.size() == 32 && id.find_first_not_of(L"0123456789abcdef") == std::wstring::npos;
}

int wmain(int argc, wchar_t** argv) {
    if (argc == 2 && wcscmp(argv[1], L"--job-preflight") == 0) return job_preflight();
    const bool fault = argc == 3 && wcscmp(argv[1], L"--crash-before-enable") == 0;
    const bool recover = argc == 4 && wcscmp(argv[1], L"--recover-disabled") == 0;
    const std::wstring id = (fault || recover) ? argv[2] : L"";
    if (argc != 1 && ((!fault && !recover) || !valid_id(id) || (recover && !valid_id(argv[3])))) {
        report("UNSUPPORTED_ARGUMENTS", ERROR_INVALID_PARAMETER); return 2;
    }
    const std::wstring tag = id.empty() ? Tag : std::wstring(Tag) + L" " + id;
    wchar_t password[44] = L"aB9!";
    bool account_created = false, policy_attempted = false, policy_installed = false;
    bool parent_created = false, root_created = false, home_created = false;
    bool cleanup_allowed = true, passed = false;
    std::wstring parent, root, home, sid, owner_marker;
    bool marker_created = false;
    PilotMutex lease;
    try {
        if (!token_elevation(GetCurrentProcess())) throw Failure{"EXPLICIT_UAC_REQUIRED", ERROR_ACCESS_DENIED};
        lease.handle.value = CreateMutexW(nullptr, FALSE, L"Global\\DevOrchestra.P2116.WfpPilot.v1");
        require(lease.handle.value != nullptr, "PILOT_MUTEX");
        const auto wait = WaitForSingleObject(lease.handle.value, 0);
        if (wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED) throw Failure{"PILOT_BUSY", ERROR_BUSY};
        lease.owned = true;
        const std::wstring receipt = fault ? L"pilot-fault-result.txt" : recover ?
            std::wstring(L"pilot-recovery-") + argv[3] + L".txt" : L"pilot-result.txt";
        open_receipt(receipt.c_str());
        if (!id.empty()) { std::printf("TRIAL=%ls\n", id.c_str()); std::fflush(stdout); }
        report("ELEVATED_PILOT_STARTED", 1);
        Engine engine;
        wchar_t data[MAX_PATH]{}, system[MAX_PATH]{};
        if (!GetEnvironmentVariableW(L"ProgramData", data, ARRAYSIZE(data)) ||
            !GetSystemDirectoryW(system, ARRAYSIZE(system))) throw Failure{"DIRECTORY_DISCOVERY", ERROR_PATH_NOT_FOUND};
        // Resolve ProgramData from the system, never from a caller-provided path.
        wchar_t volume[MAX_PATH]{};
        if (!GetWindowsDirectoryW(volume, ARRAYSIZE(volume)) || volume[1] != L':')
            throw Failure{"SYSTEM_VOLUME", ERROR_PATH_NOT_FOUND};
        const std::wstring standard = std::wstring(volume, 2) + L"\\ProgramData";
        const auto attributes = GetFileAttributesW(standard.c_str());
        if (_wcsicmp(standard.c_str(), data) != 0 || attributes == INVALID_FILE_ATTRIBUTES ||
            !(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
            throw Failure{"PROGRAMDATA_PATH", ERROR_INVALID_DATA};
        parent = standard + L"\\DevOrchestra";
        root = parent + L"\\P2116";
        if (recover) { recover_disabled_trial(engine.value, parent, root, id, tag); return 0; }
        absent(engine.value);
        if (own_account(tag)) throw Failure{"ACCOUNT_NAME_IN_USE", ERROR_ALREADY_EXISTS};
        Descriptor admins(L"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)");
        SECURITY_ATTRIBUTES access{sizeof(access), admins.data, FALSE};
        require(CreateDirectoryW(parent.c_str(), &access), "PARENT_MUST_BE_NEW");
        parent_created = true;
        if (fault) {
            owner_marker = parent + L"\\P2116-disabled-trial.id";
            trial_marker(owner_marker, id, true);
            marker_created = true;
        }
        require(CreateDirectoryW(root.c_str(), &access), "PILOT_DIRECTORY_CREATE");
        root_created = true;
        BYTE random[32]{};
        if (BCryptGenRandom(nullptr, random, sizeof(random), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0)
            throw Failure{"PASSWORD_RANDOM", ERROR_GEN_FAILURE};
        constexpr wchar_t alphabet[] = L"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-";
        for (size_t i = 0; i < sizeof(random); ++i) password[i + 4] = alphabet[random[i] & 63];
        SecureZeroMemory(random, sizeof(random));
        USER_INFO_1 user{};
        user.usri1_name = const_cast<wchar_t*>(Account); user.usri1_password = password;
        user.usri1_priv = USER_PRIV_USER; user.usri1_comment = const_cast<wchar_t*>(tag.c_str());
        user.usri1_flags = UF_SCRIPT | UF_NORMAL_ACCOUNT | UF_DONT_EXPIRE_PASSWD | UF_ACCOUNTDISABLE;
        check(NetUserAdd(nullptr, 1, reinterpret_cast<LPBYTE>(&user), nullptr), "CREATE_DISABLED_STANDARD_ACCOUNT");
        account_created = true;
        sid = account_sid();
        if (fault) {
            WSADATA winsock{};
            check(WSAStartup(MAKEWORD(2, 2), &winsock), "WINSOCK_INIT");
            const Listener allow4(AF_INET), allow6(AF_INET6);
            const auto ps = std::wstring(system) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
            policy_attempted = true;
            install(engine.value, sid, ps, allow4.port, allow6.port, tag);
            policy_installed = true;
            { Engine independent; verify_filters(independent.value, tag); }
            if (!own_account(tag, true)) throw Failure{"DISABLED_ACCOUNT_REQUIRED", ERROR_INVALID_DATA};
            report("FAULT_BEFORE_ACCOUNT_ENABLE", 1);
            require(TerminateProcess(GetCurrentProcess(), 86), "FAULT_INJECTION");
            throw Failure{"FAULT_PROCESS_STILL_RUNNING", ERROR_BUSY};
        }
        Descriptor work_access(L"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200A9;;;" + sid + L")");
        require(SetFileSecurityW(root.c_str(), DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                                 work_access.data), "PILOT_DIRECTORY_ACL");
        home = root + L"\\home";
        Descriptor home_acl(L"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1301BF;;;" + sid + L")");
        SECURITY_ATTRIBUTES home_access{sizeof(home_access), home_acl.data, FALSE};
        require(CreateDirectoryW(home.c_str(), &home_access), "PILOT_HOME_CREATE");
        home_created = true;
        WSADATA winsock{};
        check(WSAStartup(MAKEWORD(2, 2), &winsock), "WINSOCK_INIT");
        const Listener allow4(AF_INET), deny4(AF_INET), allow6(AF_INET6), deny6(AF_INET6);
        const auto ps = std::wstring(system) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
        policy_attempted = true;
        install(engine.value, sid, ps, allow4.port, allow6.port, tag);
        policy_installed = true;
        report("POLICY_INSTALLED", 16);
        { Engine independent; verify_filters(independent.value, tag); }
        report("POLICY_READBACK", 16);
        USER_INFO_1008 flags{UF_SCRIPT | UF_NORMAL_ACCOUNT | UF_DONT_EXPIRE_PASSWD};
        check(NetUserSetInfo(nullptr, Account, 1008, reinterpret_cast<LPBYTE>(&flags), nullptr), "ENABLE_STANDARD_ACCOUNT");
        PrivateDesktop desktop;
        desktop.create(sid);
        for (auto target : {&allow4, &deny4, &allow6, &deny6}) target->positive();
        report("OWNER_POSITIVE_CONTROLS", 4);
        for (auto target : {&allow4, &deny4, &allow6, &deny6}) {
            const bool allowed = target == &allow4 || target == &allow6;
            const auto marker = home + L"\\probe-stage.txt";
            if (GetFileAttributesW(marker.c_str()) != INVALID_FILE_ATTRIBUTES)
                require(DeleteFileW(marker.c_str()), "CLEAR_OWNED_PROBE_MARKER");
            const DWORD status = run_client(ps, password, root, home, sid, desktop.full_name, *target);
            const bool accepted = target->accepted();
            report("CLIENT_SCRIPT_STARTED", GetFileAttributesW(marker.c_str()) != INVALID_FILE_ATTRIBUTES);
            report("LISTENER_ACCEPTED", accepted);
            if (allowed ? (status != 0 || !accepted) : (status != 13 || accepted))
                throw Failure{"CONNECTION_EXPECTATION_FAILED", status};
            const bool v6 = target->address.ss_family == AF_INET6;
            report(allowed ? (v6 ? "IPV6_ALLOWED" : "IPV4_ALLOWED") : (v6 ? "IPV6_WSAEACCES" : "IPV4_WSAEACCES"), status);
        }
        passed = true;
    } catch (Failure error) {
        report(error.operation, error.error);
        if (!strcmp(error.operation, "PROCESS_DRAIN_UNCONFIRMED")) cleanup_allowed = false;
    } catch (...) {
        report("UNEXPECTED_FAILURE", ERROR_GEN_FAILURE);
        cleanup_allowed = false;
    }
    SecureZeroMemory(password, sizeof(password));
    try {
        if (account_created) {
            if (!own_account(tag) || (!sid.empty() && account_sid() != sid))
                throw Failure{"ACCOUNT_OWNERSHIP_CHANGED", ERROR_INVALID_DATA};
            USER_INFO_1008 flags{UF_SCRIPT | UF_NORMAL_ACCOUNT | UF_DONT_EXPIRE_PASSWD | UF_ACCOUNTDISABLE};
            check(NetUserSetInfo(nullptr, Account, 1008, reinterpret_cast<LPBYTE>(&flags), nullptr), "DISABLE_OWNED_ACCOUNT");
        }
        if (!cleanup_allowed) throw Failure{"RECOVERY_REQUIRED", ERROR_BUSY};
        if (policy_attempted) {
            Engine engine;
            if (policy_installed) remove_filters(engine.value, tag);
            else absent(engine.value);
            report("WFP_ABSENCE_VERIFIED", 1);
        }
        if (account_created) {
            if (!own_account(tag)) throw Failure{"ACCOUNT_OWNERSHIP_CHANGED", ERROR_INVALID_DATA};
            check(NetUserDel(nullptr, Account), "REMOVE_OWNED_ACCOUNT");
            if (own_account(tag)) throw Failure{"ACCOUNT_STILL_PRESENT", ERROR_BUSY};
            report("ACCOUNT_ABSENCE_VERIFIED", 1);
        }
        if (home_created) {
            const auto marker = home + L"\\probe-stage.txt";
            if (GetFileAttributesW(marker.c_str()) != INVALID_FILE_ATTRIBUTES)
                require(DeleteFileW(marker.c_str()), "REMOVE_OWNED_PROBE_MARKER");
            require(RemoveDirectoryW(home.c_str()), "REMOVE_EMPTY_PILOT_HOME");
        }
        if (root_created) require(RemoveDirectoryW(root.c_str()), "REMOVE_EMPTY_PILOT_DIRECTORY");
        if (marker_created) require(DeleteFileW(owner_marker.c_str()), "REMOVE_OWNED_TRIAL_MARKER");
        if (parent_created) require(RemoveDirectoryW(parent.c_str()), "REMOVE_EMPTY_PILOT_PARENT");
        if (root_created) report("PILOT_DIRECTORIES_REMOVED", 1);
    } catch (Failure error) { report(error.operation, error.error); return 3; }
    return passed ? 0 : 2;
}
