import sys
import warnings

if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass

warnings.simplefilter("ignore")

_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

import json  # noqa: E402
import logging  # noqa: E402
import traceback  # noqa: E402

from pydantic import TypeAdapter, ValidationError  # noqa: E402

from posprinter.core import get_service  # noqa: E402
from posprinter.models import (  # noqa: E402
    BaseResponse,
    CheckStatusRequest,
    CheckStatusResponse,
    ErrorResponse,
    GetPrintersRequest,
    GetPrintersResponse,
    PrintCalibrationImageRequest,
    PrintCalibrationTextRequest,
    PrintJobRequest,
    RequestModel,
    SuccessResponse,
)

logging.basicConfig(stream=sys.stderr, level=logging.ERROR)


def send_response(response_model: BaseResponse):
    try:
        # Використовуємо Pydantic для дампа в JSON, блядь
        json_str = response_model.model_dump_json(exclude_none=True)
        _REAL_STDOUT.write(json_str + "\n")
        _REAL_STDOUT.flush()
    except Exception as e:
        # Якщо навіть це впало, то ти повний ідіот
        sys.stderr.write(f"CRITICAL JSON ERROR: {e}\n")


def main():
    service = get_service()
    sys.stderr.write("Printer Daemon CLI Ready.\n")
    sys.stderr.flush()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            response = {}
            try:
                request = TypeAdapter(RequestModel).validate_json(line)

                if isinstance(request, GetPrintersRequest):
                    printers_list = service.get_printers()
                    response = GetPrintersResponse(data=printers_list)

                elif isinstance(request, CheckStatusRequest):
                    status_obj = service.check_status(request.connection)
                    response = CheckStatusResponse(data=status_obj)

                elif isinstance(request, PrintJobRequest):
                    service.print_job(request)
                    response = SuccessResponse()

                elif isinstance(request, PrintCalibrationImageRequest):
                    service.print_calibration_image(request)
                    response = SuccessResponse()
                elif isinstance(request, PrintCalibrationTextRequest):
                    service.print_calibration_text(request)
                    response = SuccessResponse()
                else:
                    response = ErrorResponse(
                        error="Unknown Request Type",
                        message="How the fuck did you get here?",
                    )

            except ValidationError as e:
                response = ErrorResponse(
                    error="Validation Error", details=json.loads(e.json())
                )
            except OSError as e:
                response = ErrorResponse(
                    error="Printer Error", message=f"{e.__class__.__name__}: {str(e)}"
                )
            except Exception as e:
                trace = traceback.format_exc()
                response = ErrorResponse(
                    error="System Error",
                    message=f"{e.__class__.__name__}: {str(e)}",
                    traceback=trace,
                )

            send_response(response)

    except KeyboardInterrupt:
        pass
    finally:
        service.close_all()


if __name__ == "__main__":
    main()
