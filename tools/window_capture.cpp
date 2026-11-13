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

HRESULT capture(HWND window, DWORD pid, DWORD thread, const RECT& bounds, unsigned char* pixels) {
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
        wrapped.as<IDirect3DDevice>(), DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
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
    do {
        result = context->Map(staging.get(), 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (result != DXGI_ERROR_WAS_STILL_DRAWING) break;
        if (GetTickCount64() >= deadline) return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
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
    if (argc != 7) return 2;
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
        const HRESULT status = capture(window, pid, thread, bounds, pixels.data());
        if (FAILED(status)) {
            printf("0x%08lX\n", static_cast<unsigned long>(status));
            return 4;
        }
        FILE* file{};
        if (_wfopen_s(&file, argv[6], L"wbx") || !file) return 5;
        const auto written = fwrite(pixels.data(), 1, pixels.size(), file);
        const auto closed = fclose(file);
        return written == pixels.size() && !closed ? 0 : 5;
    } catch (...) {
        printf("0x%08lX\n", static_cast<unsigned long>(to_hresult()));
        return 4;
    }
}
