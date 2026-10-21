package demo.orders;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(OrderNotFound.class)
    public ResponseEntity<Map<String, String>> orderNotFound(OrderNotFound error) {
        return ResponseEntity.status(404).body(Map.of("code", "ORDER_NOT_FOUND"));
    }
}
