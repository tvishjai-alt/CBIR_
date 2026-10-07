from pathlib import Path
from time import perf_counter
import csv

import numpy as np
from PIL import Image
from skimage.metrics import peak_signal_noise_ratio, structural_similarity
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import matplotlib.pyplot as plt



# MEMBER 2: MEDICAL IMAGE COMPRESSION AND SECURE TRANSMISSION


INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")

COMPRESSED_DIR = OUTPUT_DIR / "compressed"
ENCRYPTED_DIR = OUTPUT_DIR / "encrypted"
DECRYPTED_DIR = OUTPUT_DIR / "decrypted"

QUALITIES = [95, 90, 80, 70]

INPUT_EXTENSIONS = {".png", ".jpg", ".jpeg"}

for directory in [
    OUTPUT_DIR,
    COMPRESSED_DIR,
    ENCRYPTED_DIR,
    DECRYPTED_DIR
]:
    directory.mkdir(parents=True, exist_ok=True)



# AES-256 KEY GENERATION


aes_key = AESGCM.generate_key(bit_length=256)
aes = AESGCM(aes_key)



# FIND INPUT IMAGES


images = [
    file for file in INPUT_DIR.iterdir()
    if file.is_file() and file.suffix.lower() in INPUT_EXTENSIONS
]

if not images:
    raise FileNotFoundError(
        "No X-ray images were found in the input folder."
    )

images.sort()


# FUNCTION: IMAGE COMPRESSION


def compress_image(input_path, output_path, quality):
    image = Image.open(input_path).convert("L")

    image.save(
        output_path,
        format="JPEG",
        quality=quality,
        optimize=True
    )


# FUNCTION: AES-256 ENCRYPTION


def encrypt_file(input_path, output_path):
    data = input_path.read_bytes()

    nonce = np.random.bytes(12)

    start_time = perf_counter()

    encrypted_data = aes.encrypt(
        nonce,
        data,
        None
    )

    encryption_time = (
        perf_counter() - start_time
    ) * 1000

    output_path.write_bytes(
        nonce + encrypted_data
    )

    return encryption_time



# FUNCTION: AES-256 DECRYPTION


def decrypt_file(input_path, output_path):
    encrypted_data = input_path.read_bytes()

    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]

    start_time = perf_counter()

    decrypted_data = aes.decrypt(
        nonce,
        ciphertext,
        None
    )

    decryption_time = (
        perf_counter() - start_time
    ) * 1000

    output_path.write_bytes(
        decrypted_data
    )

    return decryption_time


# FUNCTION: IMAGE QUALITY ANALYSIS


def calculate_quality(original_path, compressed_path):

    original = np.array(
        Image.open(original_path).convert("L")
    )

    compressed = np.array(
        Image.open(compressed_path).convert("L")
    )

    psnr = peak_signal_noise_ratio(
        original,
        compressed,
        data_range=255
    )

    ssim = structural_similarity(
        original,
        compressed,
        data_range=255
    )

    return psnr, ssim



# FUNCTION: DISPLAY IMAGE COMPARISON


def display_comparison(
    original_path,
    compressed_path,
    decrypted_path,
    quality,
    compressed_size,
    reduction
):

    original = Image.open(
        original_path
    ).convert("L")

    compressed = Image.open(
        compressed_path
    ).convert("L")

    decrypted = Image.open(
        decrypted_path
    ).convert("L")

    plt.figure(figsize=(18, 7))

    plt.suptitle(
        "Member 2 - Medical Image Compression and Secure Transmission",
        fontsize=20
    )

    plt.subplot(1, 3, 1)
    plt.imshow(original, cmap="gray")
    plt.axis("off")
    plt.title(
        f"Original X-ray\n"
        f"{original_path.stat().st_size / 1024:.2f} KB"
    )

    plt.subplot(1, 3, 2)
    plt.imshow(compressed, cmap="gray")
    plt.axis("off")
    plt.title(
        f"Compressed JPEG\n"
        f"{compressed_size:.2f} KB\n"
        f"{reduction:.2f}% reduction"
    )

    plt.subplot(1, 3, 3)
    plt.imshow(decrypted, cmap="gray")
    plt.axis("off")
    plt.title(
        "After AES-256 Decryption"
    )

    plt.tight_layout()
    plt.show()



# MAIN PROCESSING


results = []

print("=" * 65)
print("MEMBER 2 - TELE-RADIOLOGY IMAGE MODULE")
print("=" * 65)

print()
print(f"Number of input images: {len(images)}")
print("AES-256 encryption key generated.")
print()


for image_index, image_path in enumerate(images):

    original_size = image_path.stat().st_size
    original_size_kb = original_size / 1024

    image = Image.open(image_path)

    width, height = image.size

    print("=" * 65)
    print(f"IMAGE: {image_path.name}")
    print(f"Resolution: {width} x {height}")
    print(f"Original size: {original_size_kb:.2f} KB")
    print("=" * 65)

    for quality in QUALITIES:

        compressed_name = (
            f"{image_path.stem}_Q{quality}.jpg"
        )

        encrypted_name = (
            f"{image_path.stem}_Q{quality}.enc"
        )

        decrypted_name = (
            f"{image_path.stem}_Q{quality}_decrypted.jpg"
        )

        compressed_path = (
            COMPRESSED_DIR / compressed_name
        )

        encrypted_path = (
            ENCRYPTED_DIR / encrypted_name
        )

        decrypted_path = (
            DECRYPTED_DIR / decrypted_name
        )

        # COMPRESSION


        compression_start = perf_counter()

        compress_image(
            image_path,
            compressed_path,
            quality
        )

        compression_time = (
            perf_counter() - compression_start
        ) * 1000

        compressed_size = (
            compressed_path.stat().st_size
        )

        compressed_size_kb = (
            compressed_size / 1024
        )

        compression_ratio = (
            original_size / compressed_size
        )

        file_reduction = (
            (original_size - compressed_size)
            / original_size
        ) * 100


        # QUALITY ANALYSIS

        psnr, ssim = calculate_quality(
            image_path,
            compressed_path
        )


        # AES-256 ENCRYPTION

        encryption_time = encrypt_file(
            compressed_path,
            encrypted_path
        )

        encrypted_size_kb = (
            encrypted_path.stat().st_size / 1024
        )


        # AES-256 DECRYPTION

        decryption_time = decrypt_file(
            encrypted_path,
            decrypted_path
        )


        # DECRYPTION VERIFICATION


        original_compressed_data = (
            compressed_path.read_bytes()
        )

        decrypted_data = (
            decrypted_path.read_bytes()
        )

        if original_compressed_data == decrypted_data:
            decryption_status = "SUCCESS"
        else:
            decryption_status = "FAILED"


        # PRINT RESULTS


        print()
        print(f"JPEG QUALITY       : {quality}")
        print(f"Compressed size    : {compressed_size_kb:.2f} KB")
        print(f"Compression ratio  : {compression_ratio:.2f}")
        print(f"File size reduction: {file_reduction:.2f}%")
        print(f"PSNR               : {psnr:.2f} dB")
        print(f"SSIM               : {ssim:.4f}")
        print(f"Compression time   : {compression_time:.3f} ms")
        print(f"Encryption time    : {encryption_time:.3f} ms")
        print(f"Decryption time    : {decryption_time:.3f} ms")
        print(f"Encrypted size     : {encrypted_size_kb:.2f} KB")
        print(f"Decryption check   : {decryption_status}")

        results.append({
            "Image": image_path.name,
            "Quality": quality,
            "Original Size (KB)": round(
                original_size_kb, 2
            ),
            "Compressed Size (KB)": round(
                compressed_size_kb, 2
            ),
            "Compression Ratio": round(
                compression_ratio, 2
            ),
            "File Size Reduction (%)": round(
                file_reduction, 2
            ),
            "PSNR (dB)": round(
                psnr, 2
            ),
            "SSIM": round(
                ssim, 4
            ),
            "Compression Time (ms)": round(
                compression_time, 3
            ),
            "Encryption Time (ms)": round(
                encryption_time, 3
            ),
            "Decryption Time (ms)": round(
                decryption_time, 3
            ),
            "Encrypted Size (KB)": round(
                encrypted_size_kb, 2
            ),
            "Decryption Status": decryption_status
        })

    print()



# SAVE RESULTS TO CSV


csv_path = OUTPUT_DIR / "member2_results.csv"

with open(
    csv_path,
    "w",
    newline="",
    encoding="utf-8"
) as csv_file:

    fieldnames = list(results[0].keys())

    writer = csv.DictWriter(
        csv_file,
        fieldnames=fieldnames
    )

    writer.writeheader()
    writer.writerows(results)



# DISPLAY REPRESENTATIVE IMAGE


representative_image = images[0]

representative_quality = 80

representative_compressed = (
    COMPRESSED_DIR
    / f"{representative_image.stem}_Q80.jpg"
)

representative_decrypted = (
    DECRYPTED_DIR
    / f"{representative_image.stem}_Q80_decrypted.jpg"
)

representative_compressed_size = (
    representative_compressed.stat().st_size / 1024
)

representative_original_size = (
    representative_image.stat().st_size
)

representative_reduction = (
    (
        representative_original_size
        - representative_compressed.stat().st_size
    )
    / representative_original_size
) * 100


display_comparison(
    representative_image,
    representative_compressed,
    representative_decrypted,
    representative_quality,
    representative_compressed_size,
    representative_reduction
)



# FINAL MESSAGE


print()
print("=" * 65)
print("MEMBER 2 CORE MODULE COMPLETED")
print("=" * 65)
print()
print("Generated:")
print("1. Compressed medical images")
print("2. AES-256 encrypted files")
print("3. Decrypted medical images")
print("4. PSNR measurements")
print("5. SSIM measurements")
print("6. Compression ratios")
print("7. File-size reduction measurements")
print("8. Compression timing")
print("9. Encryption/decryption timing")
print("10. CSV performance report")
print()
print(f"Results saved to: {OUTPUT_DIR}")
print(f"CSV report: {csv_path}")
print()
print("=" * 65)
