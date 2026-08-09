from xjobscore.inspector import config


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "xjobscore.inspector.app:app",
        host="127.0.0.1",
        port=config.port(),
        reload=False,
    )
