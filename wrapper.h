#ifndef WRAPPER_H
#define WRAPPER_H

// #include "pyncommon.h"
#include <stdbool.h>

#define API __declspec(dllexport)

#ifdef __cplusplus
extern "C" {
#endif    

//Connect, open and close dlp_nir_spectrometer.

// API int  dlpOpenByUart();

#define SLEW_SCAN_MAX_SECTIONS 5
#define ADC_DATA_LEN 864
#define WLS_NUM 228     // for 900-1700nm series
// #define WLS_NUM 160     // for 1350-2150nm series

typedef int BOOL;
typedef unsigned uint32;
typedef unsigned char uint8;
typedef unsigned short uint16;
typedef short   int16;

typedef struct
{
    uint8       scan_type;
    uint16      scanConfigIndex;
    char        scanConfig_serial_number[8];
    char        config_name[40];
    uint16      num_repeats;
    uint8       num_sections;
}PynScanConfigHead;

typedef struct
{
    uint8       section_scan_type;
    uint8       width_px;
    uint16      wavelength_start_nm;
    uint16      wavelength_end_nm;
    uint16      num_patterns;
    uint16      exposure_time;
} PynScanSection;

typedef struct
{
    PynScanConfigHead   head;
    PynScanSection      section[SLEW_SCAN_MAX_SECTIONS];
} PynScanConfig;

typedef struct
{
    uint32 header_version;
    char scan_name[20];

    //date time
    uint8     year;
    uint8     month;
    uint8     day;
    uint8     day_of_week;
    uint8     hour;
    uint8     minute;
    uint8     second;

    //body
    int16               system_temp_hundredths;
    int16               detector_temp_hundredths;
    uint16              humidity_hundredths;
    uint16              lamp_pd;
    uint32              scanDataIndex;
    double              ShiftVectorCoeffs[3];            //calibCoeffs
    double              PixelToWavelengthCoeffs[3];      //calibCoeffs
    char                serial_number[8];
    uint16              adc_data_length;
    uint8               black_pattern_first;
    uint8               black_pattern_period;
    uint8               pga;

    //slewScanConfig，占用106个字节
    PynScanConfig      cfg;

    //wavelength，intensity and length
    double              wavelength[ADC_DATA_LEN];
    int                 intensity[ADC_DATA_LEN];
    int                 length;
} PynScanResults;

typedef enum _retun_error
{
    RetSuccess                      =   0,
    RetGetSpecNumError              =  -1,
    RetInitError                    =  -2,
    RetOpenSpecError                =  -3,
    RetGetOemSpecSerialNumberError  =  -4,
    RetSetIntegrateTimeError        =  -5,
    RetGetIntegrateTimeError        =  -6,
    RetSetAvgTimesError             =  -7,
    RetGetAvgTimesError             =  -8,
    RetGetWlsError                  =  -9,
    RetGetSpecValuesError           = -10,
    RetEnumerateError               = -11,
    RetUsbConnectError              = -12,
    RetGetNumScanCfgError           = -13,
    RetGetActiveScanIndexError      = -14,
    RetSetActiveScanIndexError      = -15,
    RetSetCurrentActiveIndexError   = -16,
    RetGetScanCfgError              = -17,
    RetPerformScanError             = -18,
    RetGetFileSizeToReadError       = -19,
    RetGetFileError                 = -20,
    RetWlsLengthError               = -21,
    RetGetPgaGainError              = -22,
    RetSetPgaGainError              = -230,
    RetSetFixedPgaGainError         = -231,
    RetSetScanNumRepeatsError       = -24,
    RetSetUARTConnectedError        = -25,
    RetGetSerialNumberError         = -26,
    RetSetSerialNumberError         = -27,
    RetWlsNumIsOverFlow             = -28,
    RetInterpretError               = -29,
    RetInterpReferenceError         = -30,
    RetEnableBleError               = -31,
    RetDisableBleError              = -32,
    RetEnableButtonPressError       = -33,
    RetDisableButtonPressError      = -34
} retErrorType;


//Init hid usb
API int  dlpInitUsb();

//Open device by usb
API int  dlpOpenByUsb(int deviceIndex);

//Close device
API void dlpClose(int deviceIndex);

//Get infomation from eeprom in dlp_nir_spectrometer.
API int  dlpGetConfigInfo(PynScanConfig *config);

//Get scanResults struct
API int  dlpGetScanResults(PynScanResults *results);

//Get intensities from dlp_nir_spectrometer.
API int  dlpGetWavelengths(double *wls, int length);

//Get intensities from dlp_nir_spectrometer. The activeIndex is selected from config
API int  dlpGetIntensities(int activeIndex, int *intensity, int length);

// 获取设备内置参比强度
API int  dlpGetRefIntensityFromDevice(int *pRefIntensity, int length);

// 设置内置光源开关装填。 status: true，点亮； false, 熄灭
API int  dlpSetLampStatus(bool status);

// 设置蓝牙开关状态。status: true，开启； false，关闭
API int  dlpSetBluetoothStatus(bool status);

// 设置pga增益。固定模式，增益值gain为 1,2,4,8,16,32,64。自动模式，增益值gain为 0。
API int  dlpSetPgaGain(int gain);

// 读取pga增益
API int  dlpGetPgaGain(int *gain);

// 设置平均次数
API int  dlpSetAvgTimes(uint16 avgTimes);

//启用或关闭按钮功能
API int  dlpEnableButtonPress(bool isEnable);    

//获取按钮状态。*status = true，按键按下； *status = true， 按键未按下
API int  dlpGetButtonPressStatus(bool *pStatus);     

//读取设备UUID
API int  dlpGetDeviceUuid(uint8 *pUid);       

//读取设备湿度和温度
API int  dlpGetHumTemp(uint32 *pHumidity, int *pTemperature);   

//读取电池电压和百分比
API int  dlpGetBatteryInfo(uint32 *pVolt, uint32 *pPercent);     

API int  dlpGetDeviceStatus(uint32 *pVal);

#ifdef __cplusplus
}
#endif   

#endif // WRAPPER_H
