#include <windows.h>
#include <dwmapi.h>
#include <d3d11.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using winrt::Windows::Graphics::DirectX::DirectXPixelFormat;
using winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;

namespace {
template<class T> struct CloseOnExit {
    T& object;
    ~CloseOnExit() { try { if (object) object.Close(); } catch (...) {} }
};

bool same_window(HWND window, DWORD pid, DWORD thread) {
    DWORD actual_pid{};
    return GetWindowThreadProcessId(window, &actual_pid) == thread && actual_pid == pid;
}

struct Burst {
    int fps{};
    unsigned frames{}, superseded{};
    unsigned long long elapsed_ms{};
    long long last_frame_100ns{};
};

HRESULT capture(HWND window, DWORD pid, DWORD thread, const RECT& bounds, unsigned char* pixels, Burst& burst) {
    if (!GraphicsCaptureSession::IsSupported()) return E_NOTIMPL;
    RECT visible{};
    check_hresult(DwmGetWindowAttribute(window, DWMWA_EXTENDED_FRAME_BOUNDS, &visible, sizeof(visible)));
    if (!same_window(window, pid, thread)) return E_HANDLE;
    auto factory = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
    GraphicsCaptureItem item{nullptr};
    check_hresult(factory->CreateForWindow(window, guid_of<GraphicsCaptureItem>(), put_abi(item)));

    const auto size = item.Size();
    const LONG width = bounds.right - bounds.left, height = bounds.bottom - bounds.top;
    LONG x = 0, y = 0;
    if (size.Width == visible.right - visible.left && size.Height == visible.bottom - visible.top) {
        x = visible.left - bounds.left;
        y = visible.top - bounds.top;
    } else if (size.Width != width || size.Height != height) {
        return HRESULT_FROM_WIN32(ERROR_RETRY);
    }
    if (size.Width <= 0 || size.Height <= 0 || x < 0 || y < 0 ||
        x + size.Width > width || y + size.Height > height)
        return HRESULT_FROM_WIN32(ERROR_RETRY);

    com_ptr<ID3D11Device> device;
    com_ptr<ID3D11DeviceContext> context;
    check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr, 0, D3D11_SDK_VERSION, device.put(), nullptr, context.put()));
    com_ptr<::IInspectable> wrapped;
    check_hresult(CreateDirect3D11DeviceFromDXGIDevice(device.as<IDXGIDevice>().get(), wrapped.put()));
    auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
        wrapped.as<IDirect3DDevice>(), DirectXPixelFormat::B8G8R8A8UIntNormalized, burst.fps ? 3 : 2, size);
    CloseOnExit<Direct3D11CaptureFramePool> close_pool{pool};
    auto session = pool.CreateCaptureSession(item);
    CloseOnExit<GraphicsCaptureSession> close_session{session};
    session.IsCursorCaptureEnabled(false);
    session.StartCapture(); // Keep the OS capture border and its default consent policy.

    Direct3D11CaptureFrame frame{nullptr};
    CloseOnExit<Direct3D11CaptureFrame> close_frame{frame};
    const auto deadline = GetTickCount64() + 1500;
    while (!(frame = pool.TryGetNextFrame())) {
        if (!same_window(window, pid, thread)) return E_HANDLE;
        if (GetTickCount64() >= deadline) return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        Sleep(15);
    }
    if (burst.fps) {
        const auto start = GetTickCount64();
        burst.frames = 1;
        auto next_sample = start + 1000 / burst.fps;
        while (GetTickCount64() < start + 1000) {
            RECT current{};
            if (!same_window(window, pid, thread) || !GetWindowRect(window, &current) ||
                !EqualRect(&bounds, &current)) return HRESULT_FROM_WIN32(ERROR_RETRY);
            if (GetTickCount64() < next_sample) {
                Sleep(1);
                continue;
            }
            // Drain the bounded pool and keep only the most recent completed frame.
            auto latest = pool.TryGetNextFrame();
            if (latest) {
                if (auto newer = pool.TryGetNextFrame()) {
                    latest.Close();
                    latest = std::move(newer);
                    ++burst.superseded;
                }
                frame.Close();
                frame = std::move(latest);
                ++burst.frames;
            }
            // Increment from the start clock to avoid accumulating rounding error.
            const auto samples = (GetTickCount64() - start) * burst.fps / 1000 + 1;
            next_sample = start + (samples * 1000 + burst.fps - 1) / burst.fps;
        }
        burst.elapsed_ms = GetTickCount64() - start;
        burst.last_frame_100ns = frame.SystemRelativeTime().count();
    }
    const auto content = frame.ContentSize();
    RECT current{};
    if (!same_window(window, pid, thread) || !GetWindowRect(window, &current) ||
        !EqualRect(&bounds, &current) || content.Width != size.Width || content.Height != size.Height)
        return HRESULT_FROM_WIN32(ERROR_RETRY);

    auto access = frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
    com_ptr<ID3D11Texture2D> texture;
    check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D), texture.put_void()));
    D3D11_TEXTURE2D_DESC desc{};
    texture->GetDesc(&desc);
    if (desc.Width != static_cast<UINT>(size.Width) || desc.Height != static_cast<UINT>(size.Height) ||
        desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM || desc.SampleDesc.Count != 1) return E_UNEXPECTED;
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = desc.MiscFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    com_ptr<ID3D11Texture2D> staging;
    check_hresult(device->CreateTexture2D(&desc, nullptr, staging.put()));
    context->CopyResource(staging.get(), texture.get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    HRESULT result{};
    const auto map_deadline = GetTickCount64() + 500;
    do {
        result = context->Map(staging.get(), 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (result != DXGI_ERROR_WAS_STILL_DRAWING) break;
        if (GetTickCount64() >= map_deadline) return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        Sleep(5);
    } while (true);
    check_hresult(result);
    if (mapped.RowPitch < static_cast<UINT>(size.Width) * 4) {
        context->Unmap(staging.get(), 0);
        return E_UNEXPECTED;
    }
    memset(pixels, 0, static_cast<size_t>(width) * height * 4);
    for (int row = 0; row < size.Height; ++row)
        memcpy(pixels + (static_cast<size_t>(y + row) * width + x) * 4,
            static_cast<const unsigned char*>(mapped.pData) + static_cast<size_t>(row) * mapped.RowPitch,
            static_cast<size_t>(size.Width) * 4);
    context->Unmap(staging.get(), 0);
    return same_window(window, pid, thread) ? S_OK : E_HANDLE;
}
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 7 && argc != 8) return 2;
    Burst burst{};
    if (argc == 8) {
        if (wcscmp(argv[7], L"30") == 0) burst.fps = 30;
        else if (wcscmp(argv[7], L"60") == 0) burst.fps = 60;
        else return 2;
    }
    auto window = reinterpret_cast<HWND>(_wcstoui64(argv[1], nullptr, 10));
    DWORD pid = wcstoul(argv[2], nullptr, 10), thread = wcstoul(argv[3], nullptr, 10);
    int width = _wtoi(argv[4]), height = _wtoi(argv[5]);
    if (width < 100 || height < 100 || width > 2048 || height > 2048) return 2;
    RECT bounds{};
    if (!same_window(window, pid, thread) || !GetWindowRect(window, &bounds)) return 3;
    if (bounds.right - bounds.left != width || bounds.bottom - bounds.top != height) return 3;
    try {
        // This short-lived worker owns the COM apartment until process exit.
        init_apartment(apartment_type::multi_threaded);
        std::vector<unsigned char> pixels(static_cast<size_t>(width) * height * 4);
        const HRESULT status = capture(window, pid, thread, bounds, pixels.data(), burst);
        if (FAILED(status)) {
            printf("0x%08lX\n", static_cast<unsigned long>(status));
            return 4;
        }
        FILE* file{};
        if (_wfopen_s(&file, argv[6], L"wbx") || !file) return 5;
        const auto written = fwrite(pixels.data(), 1, pixels.size(), file);
        const auto closed = fclose(file);
        if (written != pixels.size() || closed) return 5;
        if (burst.fps) {
            LARGE_INTEGER now{}, frequency{};
            if (!QueryPerformanceCounter(&now) || !QueryPerformanceFrequency(&frequency)) return 6;
            const auto now_100ns = static_cast<long long>(now.QuadPart * 10000000.0 / frequency.QuadPart);
            const auto age_us = (now_100ns - burst.last_frame_100ns) / 10;
            printf("%u %u %llu %lld %lld\n", burst.frames, burst.superseded,
                   burst.elapsed_ms, burst.last_frame_100ns, age_us);
        }
        return 0;
    } catch (...) {
        printf("0x%08lX\n", static_cast<unsigned long>(to_hresult()));
        return 4;
    }
}
