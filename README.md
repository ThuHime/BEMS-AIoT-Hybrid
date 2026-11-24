# BEMS AIoT — ESP32-S3 + 1 PZEM thật + 19 phòng mô phỏng

Bộ mã này đã được chỉnh theo đúng số lượng linh kiện bạn vừa xác nhận:

- **01 ESP32-S3** làm bộ điều khiển.
- **01 PZEM-004T v3.0** là cảm biến đo thực tế cho **Phòng 01**.
- **19 phòng còn lại (Phòng 02–20)** được tạo bằng bộ sinh dữ liệu mô phỏng trên ESP32-S3.
- Không sử dụng 20 địa chỉ PZEM, không cần quét Modbus từ `0x01` đến `0x14`, và không cần MAX485 cho kiến trúc một PZEM TTL.
- Không có relay trong cấu hình hiện tại; Demand Response chỉ thay đổi trạng thái tải **ảo** của các phòng mô phỏng.

> **Phân biệt dữ liệu là yêu cầu bắt buộc khi báo cáo:** `simulated=false` chỉ dành cho Phòng 01 đọc từ PZEM; `simulated=true` dành cho Phòng 02–20. Không trình bày 19 phòng giả lập như dữ liệu đo thật. Phần đo điện lưới của PZEM phải được người có chuyên môn điện kiểm tra và lắp đặt; tài liệu này không hướng dẫn đấu nối điện lưới.

## 1. Cấu trúc thư mục

```text
BEMS_AIOT_1PZEM_19SIM/
├── firmware/
│   └── main.ino
├── backend/
│   └── server_ai_bems.py
├── tests/
│   ├── test_evntariff.py
│   └── test_hybrid_payload.py
├── grafana/provisioning/datasources/influxdb.yml
├── mosquitto/mosquitto.conf
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 2. Cách đọc PZEM theo đúng kiến trúc một cảm biến

PZEM-004T v3.0 sử dụng UART-TTL với giao thức dạng Modbus RTU, thông số truyền thông thông dụng là 9600 baud, 8 data bits, no parity, 1 stop bit. Thư viện `PZEM004Tv30` hỗ trợ ESP32 HardwareSerial và cho phép chọn chân RX/TX.

Trong firmware:

```cpp
static constexpr int PZEM_RX_PIN = 16;
static constexpr int PZEM_TX_PIN = 17;
PZEM004Tv30 pzem(Serial2, PZEM_RX_PIN, PZEM_TX_PIN);
```

**Ý nghĩa UART:** GPIO16 của ESP32-S3 là RX nên nhận dữ liệu từ TX của PZEM; GPIO17 là TX nên gửi yêu cầu đến RX của PZEM. Đây là giao tiếp UART TTL, không phải đường bus RS485.

### Lưu ý trước khi sử dụng bảng dây đã gửi

| Dây bạn đang ghi | Vai trò tín hiệu | Ghi chú kiểm tra |
|---|---|---|
| Xanh dương: ESP32-S3 GPIO16 | UART RX của ESP32 | Nhận dữ liệu từ TX của PZEM |
| Vàng: ESP32-S3 GPIO17 | UART TX của ESP32 | Gửi dữ liệu đến RX của PZEM |
| Xám: GND | Mass tham chiếu phía UART | Phải đối chiếu đúng chân GND trên đầu giao tiếp |
| Đỏ: 3V3 -> VCC | Nguồn giao tiếp PZEM | **Không mặc định đúng.** Nhiều PZEM v3.0 ghi chân này là `5V`; phải đọc đúng nhãn trên board/tài liệu của biến thể bạn có. Không cấp 3V3 hoặc 5V theo suy đoán. |

Thư viện chính thức của PZEM004Tv30 lưu ý chân 5V và GND của giao tiếp là cần thiết để cấp cho optocoupler. Vì vậy, với board tiêu chuẩn, không được tự coi chân nguồn giao tiếp là 3V3; hãy đọc đúng ký hiệu in trên module và đối chiếu tài liệu đúng biến thể. ESP32-S3 dùng mức logic 3.3V, GPIO không được xem là chịu được 5V. Mức TX của module cũng cần được xác minh riêng. Hãy nhờ giảng viên/kỹ thuật viên kiểm tra tương thích mức logic trước khi nối UART; không đưa tín hiệu 5V chưa chuyển mức trực tiếp vào GPIO ESP32-S3. Nếu chưa xác nhận được cả nguồn giao tiếp lẫn mức TX, chưa kết nối UART.

**Không nối MAX485 vào cấu hình này.** Module PZEM bạn có được đọc trực tiếp bằng UART TTL; MAX485 chỉ xuất hiện trong kiến trúc khác khi đã có transceiver RS485 phù hợp.

## 3. Firmware ESP32-S3

### Thư viện cần cài trong Arduino IDE

| Thư viện | Tác giả / nguồn |
|---|---|
| `PZEM004Tv30` | Jakub Mandula |
| `WiFiManager` | tzapu |
| `PubSubClient` | Nick O'Leary |
| `ArduinoJson` 6.x | Benoit Blanchon |

Cài board package `esp32` của Espressif trong Boards Manager. Chọn board ESP32-S3 đúng với model dev board đang dùng và chọn đúng cổng COM.

### Firmware làm gì?

1. Đọc V, A, W, kWh, PF và Hz của **PZEM Phòng 01**.
2. Nếu PZEM không phản hồi hoặc bất kỳ thanh ghi đo bắt buộc nào không hợp lệ, Phòng 01 được gửi `valid=false`; firmware **không bịa số đo thay thế**.
3. Tạo 19 bộ dữ liệu mô phỏng cho Phòng 02–20, có cờ `simulated=true`.
4. Đóng gói đủ 20 phòng thành một JSON.
5. Gửi lên MQTT Local; gửi tiếp lên Cloud MQTT khi đã cấu hình đúng hostname, thông tin xác thực và Root CA TLS.
6. Nhận yêu cầu Demand Response nhưng chỉ thay đổi hệ số công suất của các phòng mô phỏng 02–20. Phòng 01 không thể bị lệnh DR tác động và không có chân relay nào được điều khiển.
7. Nhấn giữ BOOT/GPIO0 khoảng 5 giây **sau khi bo mạch đã khởi động** để xóa cấu hình Wi-Fi và broker. Không giữ nút BOOT trong lúc cấp nguồn/reset.

### Wi-Fi provisioning

SSID/password Wi-Fi không nằm trong source code. Lần khởi động đầu, ESP32-S3 mở AP có tên `BEMS-ESP32S3`; mật khẩu tạm được in ra Serial Monitor. Dùng điện thoại kết nối AP và mở `192.168.4.1` nếu captive portal không tự bật. Nhập Wi-Fi nội bộ cùng địa chỉ IP máy tính đang chạy Mosquitto ở ô Local MQTT host.

Máy tính và ESP32-S3 cần ở cùng mạng LAN. Trong Docker, Mosquitto chạy nội bộ container; ESP32 phải kết nối tới **địa chỉ LAN của máy tính**, không nhập hostname `mosquitto`.

## 4. Chạy Local Server bằng Docker

1. Cài Docker Desktop và mở Docker Desktop.
2. Mở thư mục dự án.
3. Tạo file `.env` từ `.env.example`:

   Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

   macOS/Linux:

   ```bash
   cp .env.example .env
   ```

4. Sửa các mật khẩu/token mẫu trong `.env`. Không đăng tải `.env` lên GitHub.
5. Khởi động stack:

   ```bash
   docker compose up -d --build
   ```

6. Kiểm tra trạng thái:

   ```bash
   docker compose ps
   docker compose logs -f ai-bems
   ```

Các giao diện local:

- Grafana: `http://localhost:3000`
- InfluxDB: `http://localhost:8086`
- Mosquitto: cổng MQTT `1883` trong mạng LAN.

Broker mẫu đang bật anonymous access để kiểm thử trong LAN. Không forward cổng 1883 ra Internet và không dùng cấu hình anonymous cho triển khai thực tế.

## 5. JSON telemetry

Mỗi bản tin có `data_mode=hybrid_one_real_nineteen_simulated` và mảng `rooms` gồm 20 phần tử. Mẫu rút gọn:

```json
{
  "device_id": "esp32s3-bems-hybrid-01",
  "data_mode": "hybrid_one_real_nineteen_simulated",
  "physical_pzem_count": 1,
  "simulated_room_count": 19,
  "rooms": [
    {
      "id": 1,
      "source": "pzem004t_v3",
      "simulated": false,
      "valid": true,
      "voltage_v": 230.1,
      "current_a": 0.42,
      "power_w": 85.0,
      "energy_kwh": 12.345
    },
    {
      "id": 2,
      "source": "synthetic_generator",
      "simulated": true,
      "valid": true,
      "voltage_v": 230.0,
      "current_a": 0.55,
      "power_w": 110.0,
      "energy_kwh": 0.012
    }
  ]
}
```

Các số minh họa JSON ở trên chỉ là ví dụ cấu trúc; không phải số liệu của cảm biến bạn.

## 6. Python AI Engine

Backend thực hiện các bước:

- MQTT nhận telemetry ở topic `bems/telemetry`.
- Kiểm tra đủ ID phòng 01–20; lưu mỗi phòng vào InfluxDB measurement `bems_room`, kèm tag `room_id`, `simulated` và `source`.
- Tính toán năng lượng/chi phí demo theo chuỗi công suất và biểu giá đã chọn.
- Isolation Forest huấn luyện riêng theo từng phòng, gắn nhãn cảnh báo có nguồn `pzem004t_v3` hoặc `synthetic_generator`.
- LSTM dùng chuỗi tổng công suất hybrid (1 PZEM + 19 phòng mô phỏng), tổng hợp theo giờ, nhìn lại 48 giờ và dự báo 24 giờ tiếp theo.
- Demand Response chỉ phát lệnh `dry_run=true` tới phòng mô phỏng. ESP32 chỉ thay đổi dữ liệu ảo, không điều khiển tải thật.

### Giới hạn phải nêu trong báo cáo

Vì 19/20 phòng đang là dữ liệu mô phỏng, dự báo LSTM hiện là **dự báo trên bộ dữ liệu hybrid demo**, không phải kết quả dự báo đã được xác thực trên một tòa nhà có đủ 20 công tơ thật. MAPE được tính trên tập validation lấy từ chính chuỗi hybrid đó; không có cam kết MAPE dưới 5%. Để chứng minh hiệu năng thực tế, cần thay dần dữ liệu mô phỏng bằng đo đạc thật, thu thập đủ lịch sử và đánh giá trên một khoảng thời gian chưa được dùng để huấn luyện.

Isolation Forest cũng gắn cờ nguồn dữ liệu. Cảnh báo trên phòng mô phỏng chỉ chứng minh luồng thuật toán và giao diện hoạt động; không phải sự cố thật. PZEM chỉ cung cấp thông tin tiêu thụ điện, không thể tự chẩn đoán chắc chắn rò điện, hồ quang, ngắn mạch hay cháy. Đây không phải thiết bị bảo vệ điện.

## 7. Biểu giá EVN

Backend có các profile biểu giá ban hành tại Quyết định 2892/QĐ-BCT, hiệu lực từ 01/11/2026. Ví dụ:

- Sinh hoạt: 5 bậc lũy tiến 1.901 / 2.289 / 2.894 / 3.456 / 3.844 đồng/kWh.
- Cơ sở giáo dục hạ áp: 2.030 đồng/kWh.
- Sản xuất hạ áp: bình thường 1.966, thấp điểm 1.274, cao điểm 3.586 đồng/kWh.
- Kinh doanh khác hạ áp: bình thường 3.089, thấp điểm 1.880, cao điểm 5.313 đồng/kWh.

Năm bậc sinh hoạt và ba khung giờ là hai cơ chế giá khác nhau, không áp đồng thời lên cùng một lượng điện. Chọn `TARIFF_PROFILE` theo đúng nhóm khách hàng và cấp điện áp trên hợp đồng của tòa nhà. Các giá trong quyết định chưa bao gồm VAT; `VAT_RATE` mặc định bằng 0 để hiển thị phần tiền trước VAT.

## 8. InfluxDB measurements và Grafana

| Measurement | Field quan trọng | Ý nghĩa |
|---|---|---|
| `bems_room` | `power_w`, `current_a`, `voltage_v`, `energy_kwh` | Tách theo `room_id`, `simulated`, `source` |
| `bems_building` | `physical_room01_power_w` | Công suất từ PZEM thật Phòng 01 |
| `bems_building` | `simulated_rooms_power_w` | Tổng 19 phòng mô phỏng |
| `bems_building` | `hybrid_demo_total_power_w` | Tổng hybrid dùng cho demo dashboard |
| `bems_forecast` | `forecast_kwh`, `validation_mape_pct` | LSTM 24 giờ trên dữ liệu hybrid |
| `bems_alerts` | `code`, `detail`, `power_w` | Cảnh báo hành vi tiêu thụ; có tag `simulated` |
| `bems_roi` | `estimated_saving_kwh`, `estimated_saving_vnd` | ROI giả lập DR |

Trong Grafana, chọn datasource `BEMS-InfluxDB`. Nên làm panel riêng cho `physical_room01_power_w`, `simulated_rooms_power_w` và `hybrid_demo_total_power_w`, không gộp cả ba thành một đường rồi gọi toàn bộ là dữ liệu thật.

## 9. Kiểm thử nhanh

### Kiểm tra firmware

- Mở Serial Monitor ở 115200 baud.
- Khi PZEM giao tiếp được, log Phòng 01 có trạng thái `VALID` và các giá trị đọc từ PZEM.
- Nếu hiển thị `NO DATA`, không thay giá trị thực bằng dữ liệu mô phỏng. Kiểm tra cấu hình UART và nhờ người có chuyên môn xác nhận phần giao tiếp trước khi chạy tiếp.
- Kiểm tra JSON có 20 phòng, trong đó `simulated=false` chỉ xuất hiện ở ID 1.

### Kiểm tra backend

Cài Python 3.11, sau đó:

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pytest
pytest -q
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
pytest -q
```

### Kiểm tra MQTT và InfluxDB

```bash
docker compose logs -f ai-bems
```

Nếu thấy `Stored 20 room rows`, backend đã nhận đủ khung dữ liệu. Khi PZEM chưa đọc được, log sẽ thể hiện số mẫu PZEM hợp lệ bằng 0 nhưng 19 phòng mô phỏng vẫn được ghi nhận.

## 10. Nguồn kỹ thuật

- Thư viện PZEM004Tv30 và ví dụ HardwareSerial: https://github.com/mandulaj/PZEM-004T-v30
- PZEM-004T v3.0 datasheet/user manual: https://elektrologi.iptek.web.id/wp-content/uploads/2023/02/PZEM-004T-V3.0-Datasheet-User-Manual.pdf
- Quyết định 2892/QĐ-BCT (giá bán điện áp dụng từ 01/11/2026): https://www.evn.com.vn/userfile/User/tcdl/files/2026/11/1_QD-BCT_2026_2892-20261104131123509.pdf
