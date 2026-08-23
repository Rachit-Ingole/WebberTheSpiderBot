# Contributing to Webber (Spidey)

Thank you for your interest in contributing to **Webber (Spidey)**! We welcome contributions from developers, roboticists, and AI enthusiasts.

---

## 🚀 How to Contribute

### 1. Fork & Clone
1. Fork this repository on GitHub.
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/WebberTheSpiderBot.git
   cd WebberTheSpiderBot
   ```

### 2. Create a Feature Branch
Create a descriptive branch for your changes:
```bash
git checkout -b feature/awesome-new-gait
```

### 3. Guidelines & Rules

> [!IMPORTANT]  
> **Do NOT commit heavy model binaries or private data**:
> * Never commit `.pt`, `.pth`, `.onnx`, `.tflite`, or `.bin` ML weights.
> * Never commit `.venv` or Python virtual environment folders.
> * Never commit private face datasets or SQLite `.db` files.
> * Make sure any new heavy files are listed in [`.gitignore`](.gitignore).

* **Code Style**:
  * **Python**: Follow PEP 8 guidelines. Keep functions modular.
  * **Arduino / C++**: Use thread-safe `i2cMutex` locks around all I2C transactions in `sketch.ino`.
* **Testing**:
  * Test any gait modifications or IK changes carefully before deploying to physical servos to prevent hardware stalls.

### 4. Submitting a Pull Request
1. Commit your changes with a clear, concise commit message:
   ```bash
   git commit -m "Add new gait trajectory smoothing algorithm"
   ```
2. Push to your fork:
   ```bash
   git push origin feature/awesome-new-gait
   ```
3. Open a Pull Request on the main repository explaining your changes and test results.

---

## 💬 Questions or Bug Reports?
Feel free to open an Issue on GitHub for questions, feature requests, or bug reports!
