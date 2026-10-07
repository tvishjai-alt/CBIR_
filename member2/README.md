# Member 2 – Tele-Radiology Image Compression and Secure Transmission

## Objective

The objective of Member 2 is to reduce medical image size for efficient transmission while maintaining image quality and protecting the image during transmission.

## Processing Pipeline

Original X-ray
→ JPEG Compression
→ Quality Assessment
→ AES-256 Encryption
→ Secure Transmission
→ AES-256 Decryption
→ Image Reconstruction

## Implemented Features

- Medical X-ray image input
- JPEG image compression
- Compression ratio calculation
- File-size reduction calculation
- PSNR calculation
- SSIM calculation
- AES-256 encryption
- AES-256 decryption
- Encryption and decryption timing
- Verification of successful decryption
- Visual comparison of original, compressed and decrypted images

## Compression Analysis

Multiple JPEG quality levels are evaluated to study the trade-off between image size and image quality.

The implemented quality levels are:

- JPEG Quality 95
- JPEG Quality 90
- JPEG Quality 80
- JPEG Quality 70

For each quality level, the following parameters are measured:

- Compressed file size
- Compression ratio
- File-size reduction
- PSNR
- SSIM
- Compression time

## Security

AES-256 encryption is used to protect the compressed medical image before transmission.

The encrypted image is decrypted after the simulated transmission process, and the decrypted output is verified against the compressed image.

## Technologies Used

- Python
- Pillow
- NumPy
- scikit-image
- Cryptography
- Matplotlib

## Input

Chest X-ray image in PNG/JPEG format.

## Output

The module generates:

- Compressed X-ray images
- AES-256 encrypted files
- Decrypted X-ray images
- Compression and quality measurements
- Encryption/decryption timing measurements
- Visual comparison of image quality
