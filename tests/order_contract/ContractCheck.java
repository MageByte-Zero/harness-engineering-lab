package demo.orders;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

public class ContractCheck {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String MISSING = "{\"code\":\"ORDER_NOT_FOUND\",\"message\":\"订单不存在\"}";
    private static final String FOUND = "{\"id\":\"o-101\",\"status\":\"CREATED\"}";

    public static void main(String[] args) throws Exception {
        var mvc = MockMvcBuilders.standaloneSetup(new OrderController(new OrderService(new OrderRepository())))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();
        var missing = mvc.perform(get("/orders/o-missing")).andReturn().getResponse();
        var found = mvc.perform(get("/orders/o-101")).andReturn().getResponse();
        var report = JSON.createObjectNode();
        var checks = report.putArray("checks");
        checks.add(status("missing_status", 404, missing.getStatus()));
        checks.add(body("missing_body", MISSING, missing));
        checks.add(status("found_status", 200, found.getStatus()));
        checks.add(body("found_body", FOUND, found));
        System.out.println(JSON.writeValueAsString(report));
    }

    private static JsonNode status(String id, int expected, int actual) {
        return JSON.createObjectNode().put("id", id).put("expected", expected).put("actual", actual)
                .put("passed", expected == actual);
    }

    private static JsonNode body(String id, String expected, MockHttpServletResponse response) throws Exception {
        JsonNode observed;
        try {
            observed = JSON.readTree(response.getContentAsByteArray());
        } catch (com.fasterxml.jackson.core.JsonProcessingException invalid) {
            observed = null;
        }
        boolean passed = JSON.readTree(expected).equals(observed);
        return JSON.createObjectNode().put("id", id).put("expected", expected)
                .put("actual", passed ? expected : "body differs from the expected JSON").put("passed", passed);
    }
}
