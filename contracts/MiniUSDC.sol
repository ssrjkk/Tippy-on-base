// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal USDC stand-in for local tests (same ERC20 surface).
/// @dev TEST-ONLY: has an uncontrolled public `mint`. NEVER deploy this to
///      a live network — nothing in the repo does.
///      Implements the EIP-3009 surface (transferWithAuthorization) with the
///      same EIP-712 domain shape as FiatTokenV2 (name/version/chainId/
///      verifyingContract), so the x402 spec-compatibility layer can be
///      tested end to end against a token that behaves like real USDC.
contract MiniUSDC {
    string public constant name = "MiniUSDC";
    string public constant symbol = "mUSDC";
    uint8 public constant decimals = 6;
    string public constant version = "2";

    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH =
        keccak256(
            "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
        );
    bytes32 public constant DOMAIN_SEPARATOR_TYPEHASH =
        keccak256(
            "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
        );

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    // Test hook for USDC's blacklist behaviour: transfers TO a blacklisted
    // address revert (same surface as FiatTokenV2).
    mapping(address => bool) public blacklisted;
    // EIP-3009: authorizationState[authorizer][nonce] (nested, FiatTokenV2 shape).
    mapping(address => mapping(bytes32 => bool)) public authorizationState;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function setBlacklisted(address who) external {
        blacklisted[who] = true;
    }

    function domainSeparator() public view returns (bytes32) {
        return keccak256(
            abi.encode(
                DOMAIN_SEPARATOR_TYPEHASH,
                keccak256(bytes(name)),
                keccak256(bytes(version)),
                block.chainid,
                address(this)
            )
        );
    }

    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        require(block.timestamp > validAfter, "auth not yet valid");
        require(block.timestamp < validBefore, "auth expired");
        require(!authorizationState[from][nonce], "authorization is used");
        bytes32 digest = keccak256(
            abi.encodePacked(
                "\x19\x01",
                domainSeparator(),
                keccak256(
                    abi.encode(
                        TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
                        from,
                        to,
                        value,
                        validAfter,
                        validBefore,
                        nonce
                    )
                )
            )
        );
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0) && signer == from, "invalid signature");
        authorizationState[from][nonce] = true;
        emit AuthorizationUsed(from, nonce);
        require(_transfer(from, to, value), "transfer failed");
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        return _transfer(msg.sender, to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "allowance");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }
        return _transfer(from, to, amount);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) private returns (bool) {
        require(!blacklisted[to], "blacklisted");
        require(balanceOf[from] >= amount, "balance");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
        return true;
    }
}