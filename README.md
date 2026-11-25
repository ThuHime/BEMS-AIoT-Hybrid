# BEMS AIoT – ESP32-S3

**Building Energy Management System (BEMS)** là mô hình giám sát và phân tích mức tiêu thụ năng lượng trong tòa nhà. Dự án sử dụng kiến trúc **Hybrid**: một phòng đo bằng cảm biến PZEM-004T v3.0 và 19 phòng còn lại tạo dữ liệu mô phỏng bằng ESP32-S3.

> [!IMPORTANT]
> **Phân biệt dữ liệu thật và dữ liệu mô phỏng là yêu cầu bắt buộc.** Chỉ Phòng 01 sử dụng dữ liệu PZEM thật khi cảm biến đọc hợp lệ. Phòng 02–20 là dữ liệu mô phỏng. Không trình bày 19 phòng mô phỏng như 19 công tơ hoặc cảm biến đo điện thật.

## Ubuntu Server VM (OVA)

Dự án có kèm một máy ảo **Ubuntu Server đã cài đặt và cấu hình môi trường BEMS** để các thành viên trong nhóm có thể tải về, import vào VMware Workstation Pro và tiếp tục phát triển mà không cần cài đặt lại toàn bộ hệ thống.

| Thông tin | Giá trị |
|---|---|
| Tên file | `BEMS-Ubuntu-Server.ova` |
| Dung lượng | Khoảng **7.62 GiB** |
| Nền tảng | VMware Workstation Pro |
| Hệ thống bên trong | Ubuntu Server, Docker, Mosquitto, Python AI Engine, InfluxDB, Grafana và BEMS Portal |
| File kiểm tra | `BEMS-Ubuntu-Server.sha256.txt` |

**Tải máy ảo và file checksum:**

[**Mở thư mục BEMS-AIoT-Hybrid-Release trên Google Drive**](https://drive.google.com/drive/folders/11PTPNK9niT2Blyh7ru5CqPwHOPyhwNuY?usp=sharing)

### Xác minh file OVA sau khi tải

Trước khi import, tải cả `BEMS-Ubuntu-Server.ova` và `BEMS-Ubuntu-Server.sha256.txt`. Trên Windows PowerShell, chạy lệnh sau (đổi đường dẫn nếu file được lưu ở vị trí khác):

```powershell
Get-FileHash ".\BEMS-Ubuntu-Server.ova" -Algorithm SHA256
```

SHA256 chuẩn của file phát hành:

```text
9615EBD589558FB60ED2E1418F32038C89C6F8F91BAE167F98D16311529258F7
```

Chỉ tiếp tục import khi SHA256 tính được trùng khớp hoàn toàn. Nếu không khớp, tải lại file và kiểm tra lần nữa.

### Import vào VMware Workstation Pro

1. Cài và mở VMware Workstation Pro trên Windows.
2. Chọn **File → Open**, sau đó chọn `BEMS-Ubuntu-Server.ova`.
3. Đặt tên máy ảo và chọn thư mục lưu có đủ dung lượng trống.
4. Nhấn **Import** và chờ VMware hoàn tất.
5. Nếu VMware hỏi máy ảo đã được di chuyển hay sao chép, chọn **I Copied It** để tạo định danh máy ảo mới.
6. Kiểm tra **Network Adapter**. Dùng NAT để truy cập các giao diện từ máy Windows; chuyển sang Bridged nếu cần ESP32-S3 trong cùng LAN kết nối trực tiếp tới MQTT broker.
7. Khởi động Ubuntu và đăng nhập bằng thông tin tài khoản được trưởng nhóm cung cấp riêng.
8. Kiểm tra các container bằng `sudo docker ps`.

Các địa chỉ dịch vụ mặc định bên trong Ubuntu:

| Dịch vụ | Địa chỉ |
|---|---|
| BEMS Portal | `http://<UBUNTU_IP>:8088` |
| Grafana | `http://<UBUNTU_IP>:3000` |
| InfluxDB | `http://<UBUNTU_IP>:8086` |
| MQTT broker | `<UBUNTU_IP>:1883` |

Thay `<UBUNTU_IP>` bằng địa chỉ IP hiện tại của máy ảo; IP có thể khác nhau tùy cấu hình NAT/Bridged và mạng của máy nhận. Tài khoản Ubuntu, Grafana, InfluxDB và MQTT được cung cấp riêng, không ghi trong README công khai.

> [!NOTE]
> Cloud Sync Agent được để dừng trong bản OVA để tránh hai máy cùng sử dụng một checkpoint và đồng bộ trùng lên Cloudflare D1. Chỉ bật lại Cloud Sync sau khi nhóm thống nhất máy nào là Agent đồng bộ chính và đã kiểm tra cấu hình token/Worker

## 1. Tổng quan và phạm vi mô hình

### 1.1. Thành phần hệ thống

| Thành phần | Số lượng | Vai trò |
|---|---:|---|
| ESP32-S3 | 01 | Bộ điều khiển, đọc cảm biến và gửi telemetry |
| PZEM-004T v3.0 | 01 | Đo điện thực tế cho Phòng 01 |
| Phòng mô phỏng | 19 | Phòng 02–20, dữ liệu do firmware tạo |
| MQTT / Mosquitto | 01 broker | Nhận bản tin telemetry từ ESP32-S3 |
| Python AI Engine | 01 dịch vụ | Xử lý dữ liệu, cảnh báo, tính chi phí, dự báo và Demand Response mô phỏng |
| InfluxDB | 01 cơ sở dữ liệu | Lưu dữ liệu chuỗi thời gian tại máy chủ local |
| Grafana | 01 giao diện | Hiển thị dữ liệu và dashboard |
| Cloudflare Worker + D1 | Tùy chọn | Nhận và lưu bản sao dữ liệu trên Cloud |

### 1.2. Quy tắc phân biệt dữ liệu thật và dữ liệu mô phỏng

| Phòng | `room_id` | `source` | `simulated` | Ý nghĩa |
|---|---:|---|---|---|
| Phòng 01 | `1` | `pzem004t_v3` | `false` | Dữ liệu PZEM thật khi đọc hợp lệ |
| Phòng 02–20 | `2`–`20` | `synthetic_generator` | `true` | Dữ liệu mô phỏng do ESP32-S3 sinh |

- Nếu PZEM không phản hồi hoặc số đo không hợp lệ, Phòng 01 phải được đánh dấu `valid = false`.
- Không thay số đo PZEM lỗi bằng dữ liệu giả.
- Không trình bày 19 phòng mô phỏng như 19 công tơ hoặc 19 cảm biến đo điện thật.
- Mô hình hiện tại **không có relay**. Demand Response (DR) chỉ thay đổi trạng thái/hệ số tải ảo của Phòng 02–20, không đóng/ngắt thiết bị điện thật.

## 2. Kiến trúc hệ thống

```mermaid
flowchart LR
    PZEM["PZEM-004T v3.0<br/>Phòng 01"] --> ESP["ESP32-S3"]
    SIM["19 phòng mô phỏng<br/>Phòng 02–20"] --> ESP
    ESP -->|"MQTT: bems/telemetry"| MQTT["Mosquitto"]
    MQTT --> AI["Python AI Engine"]
    AI --> DB[("InfluxDB local")]
    DB --> G["Grafana Dashboard"]
    DB -. "Tùy chọn" .-> SYNC["Cloud Sync Agent"]
    SYNC --> WORKER["Cloudflare Worker API"]
    WORKER --> D1[("Cloudflare D1")]
```

### 2.1. Luồng hoạt động local

1. ESP32-S3 đọc PZEM tại Phòng 01 và tạo dữ liệu mô phỏng cho Phòng 02–20.
2. ESP32-S3 đóng gói dữ liệu thành JSON và gửi đến MQTT topic `bems/telemetry`.
3. Python AI Engine nhận bản tin, kiểm tra dữ liệu và ghi vào InfluxDB.
4. AI Engine thực hiện tính toán chi phí, phát hiện bất thường, dự báo và Demand Response mô phỏng theo cấu hình dự án.
5. Grafana truy vấn InfluxDB để hiển thị dữ liệu.
6. Nếu triển khai Cloud Backup, Cloud Sync Agent đọc dữ liệu từ InfluxDB rồi gửi qua Worker API để lưu vào D1.

> **Phân biệt hai kho dữ liệu:** InfluxDB là kho dữ liệu chuỗi thời gian local; Cloudflare D1 là bản lưu trên Cloud. ESP32-S3 không gửi trực tiếp dữ liệu đến D1 trong kiến trúc này.

## 3. Yêu cầu máy tính và phần mềm

### 3.1. Cấu hình máy tính khuyến nghị

| Thành phần | Mức khuyến nghị | Ghi chú |
|---|---|---|
| Hệ điều hành | Windows 10/11 64-bit | Docker Desktop sử dụng WSL 2 backend theo cấu hình khuyến nghị |
| RAM | 8 GB tối thiểu khuyến nghị; 16 GB trở lên tốt hơn | Chạy Docker, InfluxDB, Grafana và AI Engine đồng thời |
| CPU | Từ 4 luồng xử lý | Cần bật CPU virtualization trong BIOS/UEFI khi sử dụng WSL 2 |
| Dung lượng trống | Khoảng 15–20 GB trở lên | Image, InfluxDB volume và log sẽ chiếm dung lượng |
| Mạng | Wi-Fi/LAN nội bộ | Máy chạy Docker và ESP32-S3 phải liên lạc được qua LAN |
| USB | 01 cổng USB dữ liệu | Nạp firmware và mở Serial Monitor |

Đây là cấu hình khuyến nghị cho đồ án, không phải yêu cầu tối thiểu chính thức của mọi phiên bản Docker hoặc ESP32.

### 3.2. Phần mềm cần cài

| Phần mềm | Mục đích | Trang tải / tài liệu |
|---|---|---|
| Docker Desktop for Windows | Chạy Mosquitto, AI Engine, InfluxDB và Grafana | [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) |
| WSL 2 | Backend Linux cho Docker Desktop | [Cài WSL](https://learn.microsoft.com/windows/wsl/install) |
| Arduino IDE 2.x | Biên dịch và nạp firmware ESP32-S3 | [Arduino Software](https://www.arduino.cc/en/software) |
| Python 3.11 | Chạy test Python và công cụ hỗ trợ | [Python Downloads](https://www.python.org/downloads/windows/) |
| Git for Windows (không bắt buộc) | Tải/cập nhật mã nguồn từ GitHub | [Git for Windows](https://git-scm.com/download/win) |
| Driver USB-UART (nếu cần) | Nhận cổng COM của bo ESP32-S3 | Cài theo chip thực tế trên bo, ví dụ CP210x hoặc CH340 |

### 3.3. Cài Docker Desktop trên Windows

1. Bật CPU virtualization trong BIOS/UEFI nếu máy chưa bật.
2. Mở PowerShell bằng quyền **Run as Administrator** và chạy:

   ```powershell
   wsl --install
   wsl --update
   wsl --version
   ```

3. Khởi động lại Windows nếu hệ thống yêu cầu.
4. Tải và cài Docker Desktop for Windows từ trang chính thức.
5. Mở Docker Desktop và chờ trạng thái Engine hoạt động ổn định.
6. Trong **Settings → General**, xác nhận backend đang dùng WSL 2 nếu chọn WSL 2.
7. Mở PowerShell mới và kiểm tra:

   ```powershell
   docker --version
   docker compose version
   ```

Nếu hai lệnh trả về phiên bản, có thể tiếp tục. Yêu cầu hệ thống và giao diện cài đặt có thể thay đổi theo phiên bản Docker; xem tài liệu chính thức ở [Docker Docs](https://docs.docker.com/desktop/setup/install/windows-install/).

## 4. Nhận mã nguồn và kiểm tra thư mục

Tải mã nguồn từ [GitHub – BEMS-AIoT-Hybrid](https://github.com/ThuHime/BEMS-AIoT-Hybrid) hoặc nhận gói ZIP từ người gửi. Giải nén vào đường dẫn dễ thao tác, ví dụ:

```text
C:\BEMS_AIOT_1PZEM_19SIM\
```

### 4.1. Cấu trúc thư mục trong repository

```text
BEMS_AIOT_1PZEM_19SIM/
├── firmware/
│   └── main/
│       └── main.ino
├── backend/
│   └── server_ai_bems.py
├── tests/
│   ├── test_evntariff.py
│   └── test_hybrid_payload.py
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── influxdb.yml
├── mosquitto/
│   └── mosquitto.conf
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
├── sample_telemetry.json
└── README.md
```

> Nếu gói mã nguồn của bạn có thêm thư mục `cloud-sync/`, đó là phần mở rộng Cloud Backup và được cấu hình riêng tại [mục 15](#15-cloud-backup-lên-cloudflare-d1--tùy-chọn). Không phải bản ZIP nào cũng nhất thiết có thư mục này.

### 4.2. Kiểm tra file bằng Windows PowerShell

Mở PowerShell tại thư mục dự án:

```powershell
cd C:\BEMS_AIOT_1PZEM_19SIM

Get-ChildItem

Test-Path .\docker-compose.yml
Test-Path .\.env.example
Test-Path .\firmware\main\main.ino
Test-Path .\backend\server_ai_bems.py
```

Các lệnh `Test-Path` cần trả về `True`. Nếu có `False`, kiểm tra lại thư mục giải nén hoặc yêu cầu người gửi bổ sung file bị thiếu.

## 5. Cấu hình file môi trường `.env`

File `.env` chứa cấu hình chạy thực tế, có thể bao gồm mật khẩu, token, tài khoản và thông số InfluxDB. Repository chỉ nên lưu file mẫu `.env.example`, không lưu file `.env` thật.

### 5.1. Tạo file `.env` trên Windows

Tại thư mục gốc dự án, chạy:

```powershell
Copy-Item .env.example .env
notepad .env
```

### 5.2. Cập nhật các giá trị

1. Đọc toàn bộ `.env.example` để biết những biến môi trường mà mã nguồn sử dụng.
2. Thay các giá trị mẫu như `CHANGE_ME`, `your_password`, `replace_me` bằng giá trị riêng của máy/người triển khai.
3. Nếu có InfluxDB organization, bucket, username, password hoặc token, bảo đảm Docker Compose và AI Engine dùng cùng bộ giá trị tương ứng.
4. Không tự thêm tên biến môi trường không có trong `.env.example` hoặc mã nguồn.
5. Lưu file `.env`.

> **Không commit hoặc gửi công khai file `.env`.** Không đăng ảnh chụp màn hình có token, mật khẩu hoặc nội dung bí mật.

### 5.3. Kiểm tra cấu hình Docker Compose

```powershell
docker compose config -q
```

Nếu lệnh báo lỗi, kiểm tra cú pháp YAML, tên biến và file `.env`. Không đăng toàn bộ kết quả `docker compose config` công khai vì cấu hình đã nội suy có thể chứa thông tin nhạy cảm.

## 6. Khởi động hệ thống bằng Docker

Đảm bảo Docker Desktop đang chạy và Docker Engine đã ở trạng thái hoạt động.

### 6.1. Mở PowerShell tại thư mục dự án

```powershell
cd C:\BEMS_AIOT_1PZEM_19SIM
```

### 6.2. Xem tên service trong Compose

```powershell
docker compose config --services
```

Lệnh này in ra đúng tên các service khai báo trong `docker-compose.yml`. Dùng đúng tên hiển thị khi xem log; không tự đoán tên service.

### 6.3. Build và khởi động toàn bộ stack

```powershell
docker compose up -d --build
```

Lần chạy đầu, Docker cần tải image nền và build image backend. Thời gian phụ thuộc tốc độ mạng và cấu hình máy.

### 6.4. Kiểm tra container và log

```powershell
docker compose ps
```

Xem log gần nhất của toàn bộ stack:

```powershell
docker compose logs --tail=100
```

Theo dõi log liên tục của một service (thay `<SERVICE_NAME>` bằng tên lấy từ `docker compose config --services`):

```powershell
docker compose logs -f <SERVICE_NAME>
```

Nhấn `Ctrl + C` để thoát chế độ theo dõi log. Thao tác này không dừng container.

### 6.5. Các giao diện local

| Dịch vụ | Địa chỉ | Mục đích |
|---|---|---|
| Grafana | [http://localhost:3000](http://localhost:3000) | Dashboard giám sát |
| InfluxDB | [http://localhost:8086](http://localhost:8086) | Quản lý và truy vấn dữ liệu |
| MQTT | `IP_LAN_MAY_CHU:1883` | ESP32-S3 gửi telemetry đến Mosquitto |

Tên đăng nhập/mật khẩu Grafana và InfluxDB phải lấy từ `.env` hoặc cấu hình provisioning. Không mặc định tài khoản là `admin/admin`.

Nếu không mở được giao diện, chạy `docker compose ps`, kiểm tra cột `PORTS` và xem log service tương ứng.

## 7. Cài đặt Arduino IDE và ESP32-S3

### 7.1. Cài Arduino IDE

1. Tải Arduino IDE từ [arduino.cc](https://www.arduino.cc/en/software).
2. Cài đặt và mở Arduino IDE.
3. Kết nối ESP32-S3 bằng cáp USB có chức năng truyền dữ liệu, không dùng cáp chỉ sạc.
4. Mở **Device Manager → Ports (COM & LPT)** và ghi lại cổng COM của bo.
5. Nếu không thấy cổng, cài driver USB-UART phù hợp với chip thực tế trên bo (CP210x, CH340 hoặc loại khác), sau đó rút/cắm lại bo.

### 7.2. Cài board package ESP32 của Espressif

1. Trong Arduino IDE, chọn **File → Preferences**.
2. Tại **Additional Boards Manager URLs**, thêm URL stable chính thức:

   ```text
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```

3. Chọn **Tools → Board → Boards Manager**.
4. Tìm `esp32` và cài package **esp32 by Espressif Systems**.
5. Chờ cài đặt hoàn tất; khởi động lại Arduino IDE nếu cần.
6. Trong **Tools → Board**, chọn đúng model ESP32-S3 của bo. `ESP32S3 Dev Module` chỉ là lựa chọn chung khi phù hợp; cần đối chiếu tài liệu nhà sản xuất để chọn đúng cấu hình.

### 7.3. Cài thư viện firmware

Mở **Sketch → Include Library → Manage Libraries** và cài các thư viện:

| Thư viện | Tác giả / nguồn | Chức năng |
|---|---|---|
| `PZEM004Tv30` | Jakub Mandula | Đọc PZEM-004T v3.0 |
| `WiFiManager` | tzapu | Cấu hình Wi-Fi qua captive portal |
| `PubSubClient` | Nick O'Leary | MQTT client cho ESP32 |
| `ArduinoJson` 6.x | Benoit Blanchon | Tạo và xử lý JSON |

Cài phiên bản tương thích với source hiện tại. Nếu dự án có ghi rõ phiên bản đã kiểm thử, ưu tiên phiên bản đó.

### 7.4. Mở, biên dịch và nạp firmware

1. Trong Arduino IDE, chọn **File → Open**.
2. Mở `firmware/main/main.ino`.
3. Chọn đúng board ESP32-S3 tại **Tools → Board**.
4. Chọn đúng cổng tại **Tools → Port**.
5. Đối chiếu các tùy chọn board với model thực tế; không thay Flash/PSRAM/USB theo phỏng đoán.
6. Nhấn **Verify** (dấu tích) để biên dịch.
7. Nếu biên dịch thành công, nhấn **Upload** để nạp chương trình.
8. Mở **Tools → Serial Monitor**, đặt baud rate `115200`.
9. Nhấn Reset/EN nếu cần để xem log khởi động.

Nếu upload không thành công, kiểm tra cáp USB, cổng COM, driver và board đã chọn. Một số bo yêu cầu thao tác BOOT để vào chế độ nạp; làm theo hướng dẫn đúng model bo.

### 7.5. Kiểm tra firmware qua Serial Monitor

Kết quả mong đợi:

- Firmware khởi động và khởi tạo ESP32-S3.
- Firmware hiển thị trạng thái Wi-Fi/provisioning.
- Firmware đọc dữ liệu PZEM Phòng 01 nếu cảm biến và giao tiếp đã được kiểm tra an toàn.
- Firmware tạo dữ liệu mô phỏng cho Phòng 02–20.
- Firmware gửi telemetry lên MQTT local khi đã kết nối broker.

Nếu PZEM không có dữ liệu, Phòng 01 phải hiển thị trạng thái không hợp lệ (`valid = false` hoặc log tương đương); không thay bằng số đo mô phỏng.

## 8. Cấu hình Wi-Fi và MQTT cho ESP32-S3

### 8.1. Xác định IP LAN của máy chạy Docker

ESP32-S3 cần kết nối đến địa chỉ IP LAN của máy tính chạy Mosquitto. Không nhập `localhost`, `127.0.0.1` hoặc hostname nội bộ Docker `mosquitto` vào cấu hình ESP32.

Windows PowerShell:

```powershell
ipconfig
```

Tìm card Wi-Fi/Ethernet đang sử dụng và ghi lại dòng IPv4 Address. Ví dụ `192.168.1.50` chỉ là minh họa; hãy dùng IP thực tế của máy nhận bài.

Có thể liệt kê IPv4 bằng PowerShell:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike '127.*' -and
        $_.IPAddress -notlike '169.254*'
    }
```

### 8.2. Cấu hình Wi-Fi provisioning

Theo firmware hiện tại:

1. Khởi động ESP32-S3.
2. Khi chưa có cấu hình Wi-Fi, bo tạo Access Point tên `BEMS-ESP32S3`.
3. Mật khẩu AP tạm được in trong Serial Monitor; lấy mật khẩu từ log của chính bo mạch.
4. Dùng điện thoại/laptop kết nối AP `BEMS-ESP32S3`.
5. Nếu trang cấu hình không tự mở, truy cập `http://192.168.4.1`.
6. Nhập Wi-Fi nội bộ và mật khẩu Wi-Fi.
7. Nhập IP LAN của máy chạy Docker vào trường **Local MQTT Host**.
8. MQTT port dùng `1883` theo cấu hình local mặc định của dự án.
9. Lưu cấu hình và để ESP32-S3 kết nối lại mạng.

Máy tính và ESP32-S3 phải ở cùng LAN hoặc được định tuyến cho phép kết nối. Guest Wi-Fi có thể bật Client Isolation, khiến thiết bị không giao tiếp được với nhau.

### 8.3. Kiểm tra và mở cổng MQTT trên Windows Firewall

Docker Compose cần publish MQTT port để thiết bị ngoài Docker truy cập được, thường là `1883:1883`.

Chỉ khi ESP32-S3 không kết nối được, máy tính đang dùng mạng **Private** và bạn tin cậy mạng LAN, mở PowerShell bằng quyền Administrator rồi tạo firewall rule giới hạn trong mạng nội bộ:

```powershell
New-NetFirewallRule `
    -DisplayName 'BEMS MQTT 1883 - LAN' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 1883 `
    -RemoteAddress LocalSubnet `
    -Profile Private
```

Khi không còn sử dụng, xóa rule:

```powershell
Remove-NetFirewallRule -DisplayName 'BEMS MQTT 1883 - LAN'
```

> Không mở/forward cổng 1883 ra Internet. Cấu hình Mosquitto mẫu có thể bật anonymous access cho lab; chỉ dùng trong mạng kiểm thử riêng, không dùng cho triển khai thực tế hoặc mạng công cộng.

### 8.4. Xóa cấu hình Wi-Fi khi cần cấu hình lại

Firmware hỗ trợ nhấn giữ BOOT/GPIO0 khoảng 5 giây **sau khi bo đã khởi động** để xóa cấu hình Wi-Fi và broker đã lưu. Không giữ nút BOOT trong lúc cấp nguồn/reset nếu chưa xác nhận quy trình cho đúng model bo.

## 9. PZEM-004T v3.0 và nguyên tắc an toàn

### 9.1. Cấu hình UART trong firmware

Firmware khai báo:

```cpp
static constexpr int PZEM_RX_PIN = 16;
static constexpr int PZEM_TX_PIN = 17;

PZEM004Tv30 pzem(Serial2, PZEM_RX_PIN, PZEM_TX_PIN);
```

Ý nghĩa:

- GPIO16 là RX của ESP32-S3, nhận tín hiệu TX từ PZEM.
- GPIO17 là TX của ESP32-S3, gửi yêu cầu đến RX của PZEM.
- Kiến trúc này dùng giao tiếp UART TTL theo biến thể module; không dùng bus RS485 nên không tự ý thêm MAX485.

### 9.2. Kiểm tra giao tiếp trước khi kết nối

| Tín hiệu | Vai trò | Điều cần xác minh |
|---|---|---|
| ESP32-S3 GPIO16 | UART RX | Nối với TX giao tiếp PZEM nếu đúng sơ đồ module |
| ESP32-S3 GPIO17 | UART TX | Nối với RX giao tiếp PZEM nếu đúng sơ đồ module |
| GND | Mass tham chiếu UART | Xác định đúng chân GND trên board |
| VCC/5V/3V3 | Nguồn phần giao tiếp | Đọc nhãn đúng biến thể module và tài liệu nhà sản xuất; không suy đoán điện áp |

> [!CAUTION]
> PZEM có thể kết nối với mạch điện lưới nguy hiểm. README này **không hướng dẫn đấu nối điện lưới**. Phần đo điện lưới phải do người có chuyên môn điện lắp đặt và kiểm tra. ESP32-S3 sử dụng mức logic 3.3V; không mặc định GPIO chịu được tín hiệu TX 5V. Trước khi nối UART, phải xác minh điện áp cấp cho giao tiếp và mức logic TX/RX của đúng biến thể PZEM, đồng thời sử dụng chuyển mức phù hợp nếu cần. Nếu chưa xác minh được, không nối UART và không cấp nguồn theo phỏng đoán.

## 10. Kiểm tra dữ liệu MQTT

### 10.1. Theo dõi log AI Engine

```powershell
docker compose logs -f <SERVICE_NAME>
```

Thay `<SERVICE_NAME>` bằng tên service AI Engine lấy từ:

```powershell
docker compose config --services
```

### 10.2. Subscribe MQTT từ container Mosquitto

Nếu service trong Compose tên `mosquitto` và image có tiện ích `mosquitto_sub`, mở terminal thứ hai tại thư mục dự án:

```powershell
docker compose exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 -t 'bems/telemetry' -v
```

Nhấn `Ctrl + C` để dừng lệnh subscribe.

### 10.3. Những trường cần kiểm tra trong payload

Mỗi bản tin cần thể hiện đúng định danh thiết bị và chế độ hybrid:

- `device_id` của ESP32-S3.
- `data_mode = hybrid_one_real_nineteen_simulated`.
- `physical_pzem_count = 1`.
- `simulated_room_count = 19`.
- Mảng `rooms` có ID từ 1 đến 20.
- ID 1: `source = pzem004t_v3`, `simulated = false`.
- ID 2–20: `source = synthetic_generator`, `simulated = true`.

## 11. Kiểm tra backend và InfluxDB

### 11.1. Kiểm tra Python AI Engine

```powershell
docker compose logs --tail=100 <SERVICE_NAME>
```

Khi có telemetry, log cần cho thấy backend nhận và xử lý bản tin. Nếu PZEM chưa hợp lệ, 19 phòng mô phỏng vẫn có thể được ghi nhận; không tạo số đo thật cho Phòng 01.

### 11.2. Mở InfluxDB

Truy cập [http://localhost:8086](http://localhost:8086) và đăng nhập bằng thông tin đã cấu hình trong `.env` hoặc cấu hình khởi tạo.

Các measurement chính:

| Measurement | Nội dung |
|---|---|
| `bems_room` | Điện áp, dòng điện, công suất, điện năng theo phòng; có tag `room_id`, `simulated`, `source` |
| `bems_building` | Dữ liệu tổng hợp cấp tòa nhà; tách công suất Phòng 01 và 19 phòng mô phỏng |
| `bems_forecast` | Kết quả dự báo điện năng và chỉ số đánh giá mô hình |
| `bems_alerts` | Sự kiện cảnh báo và thông tin nguồn dữ liệu |
| `bems_roi` | Sự kiện ước tính tiết kiệm từ DR mô phỏng |
| `bems_device_status` | Trạng thái thiết bị ESP32-S3 |
| `bems_energy` | Dữ liệu năng lượng, chi phí và các giá trị liên quan theo cấu hình backend |

### 11.3. Ví dụ truy vấn Flux – công suất theo phòng

Trong InfluxDB, mở **Data Explorer**, chọn Flux và điều chỉnh bucket nếu `.env` dùng tên khác. Ví dụ với bucket `bems`:

```flux
from(bucket: "bems")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "bems_room")
  |> filter(fn: (r) => r._field == "power_w")
  |> yield(name: "room_power")
```

Để phân biệt phòng thật và mô phỏng, xem các tag `room_id`, `simulated` và `source` trong kết quả.

### 11.4. Kiểm tra dữ liệu cấp tòa nhà

```flux
from(bucket: "bems")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "bems_building")
```

Các trường cần quan sát gồm `physical_room01_power_w`, `simulated_rooms_power_w` và `hybrid_demo_total_power_w`. Không gộp tất cả rồi gọi là công suất đo thật.

## 12. Truy cập Grafana và xem dashboard

1. Mở trình duyệt trên máy chạy Docker.
2. Truy cập [http://localhost:3000](http://localhost:3000).
3. Đăng nhập bằng tài khoản được cấu hình trong `.env` hoặc provisioning.
4. Mở **Connections → Data sources** (tên menu có thể thay đổi theo phiên bản Grafana).
5. Kiểm tra datasource `BEMS-InfluxDB` đã được provision và kết nối thành công.
6. Mở dashboard BEMS được tạo/đóng gói trong dự án.

Datasource chạy bên trong Docker network thường cần URL nội bộ như `http://influxdb:8086`, không phải `http://localhost:8086`. Đối chiếu cấu hình tại `grafana/provisioning/datasources/influxdb.yml`.

Dashboard nên tách ít nhất ba nhóm dữ liệu:

- Công suất Phòng 01 – PZEM thật.
- Tổng công suất 19 phòng mô phỏng.
- Tổng công suất hybrid phục vụ demo.

Nếu dashboard không xuất hiện, kiểm tra thư mục provisioning, log Grafana và datasource. Không xóa Docker volume khi đang cần giữ dữ liệu.

## 13. Cài Python và chạy kiểm thử

Phần này chạy test độc lập với Docker. Dự án sử dụng Python 3.11 theo môi trường đã kiểm thử.

### 13.1. Kiểm tra Python

```powershell
py -3.11 --version
```

Nếu chưa có Python 3.11, cài từ [python.org](https://www.python.org/downloads/windows/) và mở terminal mới.

### 13.2. Tạo virtual environment trên Windows

Tại thư mục gốc dự án:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pytest
```

Nếu PowerShell chặn script kích hoạt môi trường, chỉ áp dụng cho cửa sổ PowerShell hiện tại:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 13.3. Chạy test

Khi virtual environment đang được kích hoạt:

```powershell
python -m pytest -q
```

Các test hiện có:

- `tests/test_evntariff.py`: kiểm tra logic biểu giá điện.
- `tests/test_hybrid_payload.py`: kiểm tra cấu trúc/logic payload hybrid.

Nếu test fail, lưu thông báo lỗi đầy đủ để xác định nguyên nhân; không bỏ qua test lỗi khi báo cáo kết quả.

## 14. Khởi động, dừng và chạy lại hệ thống

| Thao tác | Lệnh PowerShell |
|---|---|
| Khởi động stack | `docker compose up -d` |
| Build lại và khởi động | `docker compose up -d --build` |
| Xem trạng thái | `docker compose ps` |
| Xem log 100 dòng gần nhất | `docker compose logs --tail=100` |
| Theo dõi log service | `docker compose logs -f <SERVICE_NAME>` |
| Dừng container, giữ dữ liệu | `docker compose stop` |
| Dừng và xóa container/network của project | `docker compose down` |

> [!WARNING]
> Không chạy `docker compose down -v` nếu chưa chủ động muốn xóa volume dữ liệu. Tùy cấu hình Compose, thao tác này có thể làm mất dữ liệu InfluxDB và các volume liên quan.

## 15. Cloud Backup lên Cloudflare D1 (tùy chọn)

<details>
<summary><strong>Mở hướng dẫn triển khai Cloud Backup (Windows)</strong></summary>

> Phần này chỉ áp dụng khi gói mã nguồn có thư mục `cloud-sync/` và đã có Worker/D1 được cấu hình. Nếu thư mục hoặc Worker chưa được bàn giao, cần bổ sung chúng trước khi chạy các lệnh bên dưới.

### 15.1. Kiến trúc Cloud Backup

```text
InfluxDB local
    │
    ▼
Cloud Sync Agent (Python + Docker)
    │ HTTPS + SYNC_API_TOKEN
    ▼
Cloudflare Worker API (/ingest)
    │ D1 binding: DB
    ▼
Cloudflare D1
```

Cloud Sync Agent đọc dữ liệu từ InfluxDB, gom theo phút rồi gửi tới Worker endpoint. Worker xác thực token và ghi dữ liệu vào D1. Không đưa token vào source code hoặc Git.

### 15.2. Yêu cầu phía Cloudflare

Cần chuẩn bị:

1. Cloudflare D1 database.
2. Worker có binding tên `DB` trỏ đến database.
3. Worker secret tên `SYNC_API_TOKEN`.
4. API có `GET /health` và `POST /ingest` theo đúng phiên bản Worker của dự án.
5. URL Worker dạng `https://<worker-name>.<subdomain>.workers.dev`.

Kiểm tra health endpoint trong trình duyệt:

```text
https://<worker-name>.<subdomain>.workers.dev/health
```

### 15.3. Cấu trúc thư mục Cloud Sync

```text
cloud-sync/
├── Dockerfile
├── requirements.txt
├── sync_agent.py
├── compose.yaml
└── data/
```

### 15.4. Tạo file token riêng trên Windows

Mở PowerShell. Tạo thư mục chứa bí mật **bên ngoài source code**:

```powershell
New-Item -ItemType Directory -Force -Path C:\BEMS\secrets | Out-Null
```

Nhập InfluxDB read token mà không hiển thị ký tự khi gõ:

```powershell
$influxSecure = Read-Host "Nhap InfluxDB read token" -AsSecureString
$influxPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($influxSecure)
try {
    $influxPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($influxPtr)
    [IO.File]::WriteAllText("C:\BEMS\secrets\influx_token", $influxPlain)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($influxPtr)
    Remove-Variable influxPlain -ErrorAction SilentlyContinue
    Remove-Variable influxSecure -ErrorAction SilentlyContinue
}
```

Nhập Cloudflare `SYNC_API_TOKEN`:

```powershell
$cloudSecure = Read-Host "Nhap Cloudflare SYNC_API_TOKEN" -AsSecureString
$cloudPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($cloudSecure)
try {
    $cloudPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($cloudPtr)
    [IO.File]::WriteAllText("C:\BEMS\secrets\cloud_sync_token", $cloudPlain)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($cloudPtr)
    Remove-Variable cloudPlain -ErrorAction SilentlyContinue
    Remove-Variable cloudSecure -ErrorAction SilentlyContinue
}
```

Hai file chỉ chứa token, không thêm dấu nháy hoặc dòng cấu hình khác. Không mở/hiển thị nội dung token và không đưa `C:\BEMS\secrets` vào ZIP/Git.

### 15.5. Điều chỉnh bind mount trong `cloud-sync/compose.yaml`

Nếu file Compose gốc dùng đường dẫn kiểu `${HOME}/.config/bems/...`, thay riêng phần `volumes:` của service `cloud-sync` bằng:

```yaml
volumes:
  - ./data:/app/data
  - type: bind
    source: C:/BEMS/secrets/influx_token
    target: /run/secrets/influx_token
    read_only: true
  - type: bind
    source: C:/BEMS/secrets/cloud_sync_token
    target: /run/secrets/cloud_sync_token
    read_only: true
```

Giữ nguyên các biến môi trường, network, giới hạn bảo mật và thiết lập khác trong file. Nếu secret ở thư mục khác, thay đường dẫn `C:/BEMS/secrets` bằng đường dẫn thực tế. Không tạo thêm mount thứ hai trỏ đến cùng target.

Kiểm tra Docker network:

```powershell
docker network ls
```

Tên external network trong `cloud-sync/compose.yaml` phải trùng network mà stack BEMS chính đang sử dụng. Nếu khác, điều chỉnh trường `name:` trong khai báo external network. Không tự tạo network khác nếu Agent cần truy cập service InfluxDB đang chạy.

### 15.6. Khởi động Cloud Sync Agent

```powershell
cd C:\BEMS_AIOT_1PZEM_19SIM\cloud-sync
New-Item -ItemType Directory -Force -Path .\data | Out-Null
docker compose config -q
docker compose up -d --build cloud-sync
```

Kiểm tra container và log:

```powershell
docker ps --filter "name=bems-cloud-sync"
docker logs --tail 100 bems-cloud-sync
```

Theo dõi log liên tục:

```powershell
docker logs -f bems-cloud-sync
```

Nhấn `Ctrl + C` để ngừng theo dõi log, không dừng container. Kiểm tra checkpoint local:

```powershell
Get-Content .\data\sync_state.json
```

Nếu Docker không mount được file, kiểm tra hai file token có tồn tại đúng đường dẫn và Docker Desktop được phép truy cập ổ đĩa/thư mục. Nếu Agent không kết nối được InfluxDB, kiểm tra external network và các biến `INFLUX_URL`, `INFLUX_ORG`, `INFLUX_BUCKET` trong `cloud-sync/compose.yaml`.

### 15.7. Giới hạn của Cloud Sync Agent

- Agent bắt đầu đồng bộ từ thời điểm khởi tạo state, không tự động backfill toàn bộ lịch sử InfluxDB trước thời điểm đó.
- Agent có safety lag để tránh đọc phút dữ liệu chưa hoàn tất.
- Nếu Worker/API lỗi, Agent retry và không tăng checkpoint cho phút đồng bộ thất bại. Khi API hoạt động lại, Agent có thể tiếp tục xử lý phần còn thiếu nếu dữ liệu nguồn vẫn còn.
- Dữ liệu được lưu theo khóa/upsert để hạn chế bản ghi trùng.
- Forecast được đồng bộ theo chu kỳ riêng, không nhất thiết xuất hiện trong mọi chu kỳ telemetry.
- Không kết luận đã kiểm thử mất toàn bộ Internet nếu chỉ giả lập URL Worker không truy cập được.

### 15.8. Xem dữ liệu trên Cloudflare D1

1. Đăng nhập [Cloudflare Dashboard](https://dash.cloudflare.com/).
2. Mở **Storage & databases → D1 SQL Database**.
3. Chọn database và mở **Console**.
4. Chạy truy vấn đếm bản ghi:

```sql
SELECT 'room_telemetry_minute' AS table_name, COUNT(*) AS total_rows FROM room_telemetry_minute
UNION ALL SELECT 'building_telemetry_minute', COUNT(*) FROM building_telemetry_minute
UNION ALL SELECT 'alerts', COUNT(*) FROM alerts
UNION ALL SELECT 'forecasts', COUNT(*) FROM forecasts
UNION ALL SELECT 'roi_events', COUNT(*) FROM roi_events
UNION ALL SELECT 'device_status', COUNT(*) FROM device_status
UNION ALL SELECT 'sync_checkpoint', COUNT(*) FROM sync_checkpoint;
```

Xem dữ liệu phòng mới nhất:

```sql
SELECT
    minute_utc,
    COUNT(*) AS room_records,
    COUNT(DISTINCT room_id) AS distinct_rooms,
    SUM(sample_count) AS total_samples
FROM room_telemetry_minute
GROUP BY minute_utc
ORDER BY minute_utc DESC
LIMIT 10;
```

Kiểm tra bản ghi trùng:

```sql
SELECT
    minute_utc,
    room_id,
    simulated,
    COUNT(*) AS duplicate_count
FROM room_telemetry_minute
GROUP BY minute_utc, room_id, simulated
HAVING COUNT(*) > 1;
```

Truy vấn không trả kết quả là dấu hiệu tốt trong phạm vi dữ liệu đã kiểm tra; vẫn cần xem checkpoint và log Agent để xác nhận đồng bộ liên tục.

</details>

## 16. Quy trình chạy dự án từ đầu đến cuối

| Bước | Công việc | Kết quả cần đạt |
|---:|---|---|
| 1 | Cài Docker Desktop, WSL 2, Arduino IDE và Python 3.11 | Công cụ sẵn sàng |
| 2 | Tải/giải nén source vào thư mục trên Windows | Có đủ file dự án |
| 3 | Tạo `.env` từ `.env.example` và tự cấu hình | Compose có đủ biến môi trường |
| 4 | Chạy `docker compose config -q` | Cấu hình Compose hợp lệ |
| 5 | Chạy `docker compose up -d --build` | Các dịch vụ local được khởi động |
| 6 | Mở Grafana/InfluxDB, kiểm tra container và log | Backend và giao diện hoạt động |
| 7 | Cài board package/thư viện và nạp `firmware/main/main.ino` nếu có phần cứng | ESP32-S3 chạy firmware |
| 8 | Cấu hình Wi-Fi và Local MQTT Host theo IP LAN của máy nhận bài | ESP32-S3 kết nối broker |
| 9 | Kiểm tra MQTT, AI Engine, InfluxDB và dashboard | Telemetry được xử lý và hiển thị |
| 10 | Chạy `python -m pytest -q` | Có kết quả kiểm thử |
| 11 | Nếu dùng Cloud Backup, cấu hình Cloudflare Worker/D1 và token riêng | Agent đồng bộ dữ liệu lên Cloud |

Mỗi máy nhận bài cần tự cấu hình thông tin môi trường, địa chỉ LAN, tài khoản và token riêng. Không sử dụng lại IP hoặc credential của máy gửi bài.

## 17. Xử lý lỗi thường gặp

<details>
<summary><strong>Docker không nhận lệnh hoặc Docker daemon không chạy</strong></summary>

- Mở Docker Desktop và chờ Engine chạy.
- Mở PowerShell mới, kiểm tra `docker --version` và `docker compose version`.
- Nếu dùng WSL 2, kiểm tra `wsl --version`; khởi động lại Docker Desktop nếu backend bị lỗi.
</details>

<details>
<summary><strong>docker compose báo thiếu biến môi trường</strong></summary>

- Tạo `.env` từ `.env.example`.
- Điền các giá trị bắt buộc.
- Chạy lại `docker compose config -q`.
</details>

<details>
<summary><strong>Container thoát liên tục</strong></summary>

```powershell
docker compose ps -a
docker compose logs --tail=200 <SERVICE_NAME>
```

Kiểm tra cấu hình, biến môi trường, quyền truy cập và thông báo lỗi của service.
</details>

<details>
<summary><strong>ESP32-S3 không xuất hiện cổng COM hoặc Arduino upload thất bại</strong></summary>

- Dùng cáp USB dữ liệu.
- Thử cổng USB khác.
- Cài đúng driver USB-UART theo chip trên bo.
- Kiểm tra board và COM đã chọn trong Arduino IDE.
- Làm theo hướng dẫn chế độ nạp của đúng model bo.
</details>

<details>
<summary><strong>ESP32-S3 không kết nối MQTT</strong></summary>

- Kiểm tra IP LAN bằng `ipconfig`.
- Không dùng `localhost`, `127.0.0.1` hoặc hostname Docker `mosquitto` trong cấu hình ESP32.
- Kiểm tra `docker compose ps`, port `1883`, firewall và log Mosquitto.
- Kiểm tra máy tính và ESP32 có cùng mạng LAN, không bị Client Isolation.
</details>

<details>
<summary><strong>PZEM hiển thị NO DATA hoặc Phòng 01 không hợp lệ</strong></summary>

- Giữ trạng thái `valid = false`; không thay số đo bằng dữ liệu mô phỏng.
- Kiểm tra cấu hình UART, module, nguồn giao tiếp và tương thích mức logic với sự hỗ trợ của người có chuyên môn.
- Không tự thử đấu nối điện lưới theo README.
</details>

<details>
<summary><strong>Grafana không có dữ liệu hoặc không kết nối InfluxDB</strong></summary>

- Kiểm tra datasource provisioning và log Grafana.
- Kiểm tra AI Engine có nhận telemetry và ghi dữ liệu hay không.
- Trong Docker network, datasource thường sử dụng `http://influxdb:8086`, không phải `http://localhost:8086`.
- Kiểm tra time range của dashboard và Data Explorer.
</details>

<details>
<summary><strong>Cloud Sync không kết nối Worker hoặc InfluxDB</strong></summary>

- Kiểm tra Worker URL, endpoint `/health` và token mà không in token ra log.
- Kiểm tra external Docker network và `INFLUX_URL`, `INFLUX_ORG`, `INFLUX_BUCKET`.
- Xem log bằng `docker logs --tail 100 bems-cloud-sync`.
</details>

## 18. Giới hạn và lưu ý khi báo cáo

### 18.1. Dữ liệu đo thật và mô phỏng

Chỉ Phòng 01 sử dụng PZEM. Phòng 02–20 là dữ liệu mô phỏng. Dashboard và báo cáo phải gắn nhãn rõ **Physical/Real** và **Simulated**.

### 18.2. Dự báo AI

LSTM trong cấu hình hiện tại dự báo trên chuỗi công suất hybrid (Phòng 01 nếu PZEM hợp lệ + 19 phòng mô phỏng). Vì phần lớn dữ liệu là mô phỏng, đây là dự báo trên bộ dữ liệu hybrid demo, không phải đánh giá dự báo thực tế cho tòa nhà có đủ 20 công tơ thật. MAPE/MAE/RMSE phải được trình bày theo tập validation thực sự sử dụng; không tự cam kết MAPE dưới 5% nếu không có kết quả kiểm thử chứng minh.

### 18.3. Isolation Forest và cảnh báo

Cảnh báo trên phòng mô phỏng chứng minh luồng xử lý thuật toán và giao diện, không chứng minh đã phát hiện sự cố vật lý thật. PZEM đo các thông số điện năng tiêu thụ; bản thân nó không thể kết luận chắc chắn có rò điện, hồ quang, ngắn mạch hoặc cháy. PZEM không thay thế thiết bị bảo vệ điện.

### 18.4. Demand Response

Mô hình không có relay. DR chỉ thay đổi tải/hệ số tải ảo của Phòng 02–20. Không mô tả đây là hệ thống tự động đóng/ngắt thiết bị điện thật.

### 18.5. Biểu giá điện

Các mức dưới đây theo Quyết định **2892/QĐ-BCT ngày 31/10/2026**, áp dụng từ **01/11/2026**. Giá trong phụ lục chưa bao gồm VAT.

| Nhóm/Profile | Cấp điện áp / khung giá | Giá (đồng/kWh) |
|---|---|---:|
| Sinh hoạt – Bậc 1 | Đến 100 kWh | 1.901 |
| Sinh hoạt – Bậc 2 | 101–200 kWh | 2.289 |
| Sinh hoạt – Bậc 3 | 201–400 kWh | 2.894 |
| Sinh hoạt – Bậc 4 | 401–700 kWh | 3.456 |
| Sinh hoạt – Bậc 5 | Từ 701 kWh trở lên | 3.844 |
| Cơ sở giáo dục | Hạ áp đến 1 kV | 2.030 |
| Cơ sở giáo dục | Trung áp trở lên | 1.901 |
| Sản xuất | Hạ áp – giờ bình thường | 1.966 |
| Sản xuất | Hạ áp – giờ thấp điểm | 1.274 |
| Sản xuất | Hạ áp – giờ cao điểm | 3.586 |
| Kinh doanh khác | Hạ áp – giờ bình thường | 3.089 |
| Kinh doanh khác | Hạ áp – giờ thấp điểm | 1.880 |
| Kinh doanh khác | Hạ áp – giờ cao điểm | 5.313 |

**Lưu ý:** Năm bậc sinh hoạt và ba khung giờ là hai cơ chế giá khác nhau, không áp đồng thời lên cùng một lượng điện. Chọn `TARIFF_PROFILE` theo đúng nhóm khách hàng và cấp điện áp trên hợp đồng. Các mức giá trong bảng chưa gồm VAT. Xem quyết định và biểu giá hiện hành tại [EVN – Quyết định 2892/QĐ-BCT](https://www.evn.com.vn/d/vi-VN/news/Quyet-dinh-so-2892QD-BCT-ngay-31102026-cua-Bo-Cong-Thuong-quy-dinh-ve-gia-ban-dien-60-3569-510199) và [phụ lục giá bán điện](https://evn.com.vn/userfile/User/tcdl/files/2026/11/1_QD-BCT_2026_2892-20261104131123509.pdf).

### 18.6. Cloud Backup và Disaster Recovery

Đồng bộ InfluxDB lên Cloudflare D1 chứng minh luồng lưu bản sao dữ liệu lên Cloud. Điều đó **chưa tự động chứng minh Disaster Recovery hoàn chỉnh**. Để kết luận có DR, cần có quy trình và kiểm thử khôi phục dữ liệu từ Cloud về môi trường mới, đồng thời kiểm tra tính toàn vẹn dữ liệu và thời gian khôi phục.

## Ghi chú phát hành

- Repository: [ThuHime/BEMS-AIoT-Hybrid](https://github.com/ThuHime/BEMS-AIoT-Hybrid)
- Firmware chính: `firmware/main/main.ino`
- Backend: `backend/server_ai_bems.py`
- Compose local: `docker-compose.yml`
- Cấu hình Grafana datasource: `grafana/provisioning/datasources/influxdb.yml`

Khi cập nhật source hoặc cấu hình, hãy cập nhật README tương ứng và không commit `.env`, token, mật khẩu, khóa riêng hoặc dữ liệu runtime.
