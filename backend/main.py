"""AiceMind Admin Backend 启动入口"""
import uvicorn


def main():
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=5011,
        log_level="info",
        access_log=True,
        reload=False,
    )


if __name__ == "__main__":
    main()
